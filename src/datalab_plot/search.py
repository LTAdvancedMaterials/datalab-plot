"""Discover items on a datalab instance."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .client import DatalabPlotClient, _resolve_client

logger = logging.getLogger(__name__)

# How many full-item documents to fetch in parallel when enriching cells.
_ENRICH_WORKERS = 8


def _format_constituents(value: object) -> str:
    """Compact ``'A + B'`` summary of a cell electrode / electrolyte list.

    datalab stores ``positive_electrode`` / ``negative_electrode`` /
    ``electrolyte`` as lists of *constituent* dicts; each has an ``item``
    reference carrying a ``chemform`` and/or ``name``.
    """
    if not isinstance(value, (list, tuple)) or not value:
        return ""
    names: list[str] = []
    for cons in value:
        if isinstance(cons, str):
            if cons:
                names.append(cons)
            continue
        if not isinstance(cons, dict):
            continue
        item = cons.get("item")
        name = ""
        if isinstance(item, dict):
            name = item.get("chemform") or item.get("name") or ""
        name = name or cons.get("chemform") or cons.get("name") or ""
        if name:
            names.append(str(name))
    return " + ".join(names)


def extract_cathode_mass_mg(item_dict: dict) -> float | None:
    """Sum all ``quantity`` values on positive-electrode constituents (units: mg).

    Returns ``None`` when no positive-electrode constituents carry a numeric
    quantity (i.e. the cell record has no mass data).
    """
    pos = item_dict.get("positive_electrode") or []
    if not isinstance(pos, list):
        return None
    total = 0.0
    found = False
    for cons in pos:
        if not isinstance(cons, dict):
            continue
        qty = cons.get("quantity")
        if qty is None:
            continue
        try:
            total += float(qty)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def _row_from_item(it: dict) -> dict:
    collections = it.get("collections") or []
    coll_names = ", ".join(
        (cc.get("collection_id") or cc.get("immutable_id") or "")
        for cc in collections
        if isinstance(cc, dict)
    )
    return {
        "item_id": it.get("item_id") or "",
        "name": it.get("name") or "",
        "refcode": it.get("refcode") or "",
        "type": it.get("type") or "",
        # datalab names this `characteristic_chemical_formula` on both the
        # list summary and the full item — there is no `chemform` key.
        "chemform": it.get("characteristic_chemical_formula") or it.get("chemform") or "",
        "positive_electrode": _format_constituents(it.get("positive_electrode")),
        "negative_electrode": _format_constituents(it.get("negative_electrode")),
        "electrolyte": _format_constituents(it.get("electrolyte")),
        # The list endpoint carries `date`; the full item carries `last_modified`.
        "last_modified": it.get("last_modified") or it.get("date") or "",
        "collections": coll_names,
        "cathode_mass_mg": extract_cathode_mass_mg(it),
    }


def _as_item_list(result: object) -> list:
    """Coerce a ``get_items`` / ``search_items`` response into a list of items.

    ``datalab_api.get_items`` returns the raw endpoint dict (e.g.
    ``{"samples": [...], "status": ...}``) when the response shape doesn't
    match the keys it knows how to unwrap — so handle that here rather than
    silently dropping the items.
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("items", "samples"):
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


def _enrich_cells(client: DatalabPlotClient, items: list) -> list:
    """Replace cell summary dicts with their full item documents.

    The ``/samples`` list and ``/search-items`` endpoints omit the cell
    construction fields (``positive_electrode`` / ``negative_electrode`` /
    ``electrolyte``) — only the per-item ``get-item-data`` endpoint carries
    them. Fetch the full document for each ``type == "cells"`` item, in
    parallel. Non-cell items and any fetch that fails are left unchanged.
    """
    cells = [
        (i, it)
        for i, it in enumerate(items)
        if isinstance(it, dict) and it.get("type") == "cells" and it.get("item_id")
    ]
    if not cells:
        return items

    def _fetch(idx_item: tuple[int, dict]) -> tuple[int, dict]:
        idx, it = idx_item
        try:
            return idx, client.client.get_item(item_id=it["item_id"])
        except Exception:
            logger.warning(
                "Could not fetch full item %s for enrichment; "
                "construction fields will be blank",
                it.get("item_id"),
                exc_info=True,
            )
            return idx, it

    enriched = list(items)
    with ThreadPoolExecutor(max_workers=_ENRICH_WORKERS) as pool:
        for idx, full in pool.map(_fetch, cells):
            enriched[idx] = full
    return enriched


def find_cells(
    query: str | None = None,
    *,
    item_type: str | tuple[str, ...] = ("samples", "cells"),
    limit: int | None = None,
    client: DatalabPlotClient | None = None,
    enrich: bool = True,
) -> pd.DataFrame:
    """List items available to the authenticated user as a DataFrame.

    With ``query`` set, calls ``/search-items``. Without, calls
    ``/<item_type>`` per requested type and returns everything.

    Returned columns: ``item_id``, ``name``, ``refcode``, ``type``,
    ``chemform``, ``positive_electrode``, ``negative_electrode``,
    ``electrolyte``, ``last_modified``, ``collections``.

    ``enrich`` (default ``True``): the datalab list / search endpoints do
    not return cell construction fields (electrodes / electrolyte), so each
    ``cells`` item is fetched individually to fill them in. This costs one
    extra request per cell (issued in parallel). Pass ``enrich=False`` to
    skip it when those columns aren't needed — the columns are then blank.
    """
    types = (item_type,) if isinstance(item_type, str) else tuple(item_type)

    c, owns = _resolve_client(client)
    try:
        if query:
            items = list(_as_item_list(c.client.search_items(query, item_types=types)))
        else:
            # search_items takes multiple types in one call; get_items does not.
            items = []
            seen: set[str] = set()
            for t in types:
                try:
                    result = c.client.get_items(item_type=t)
                except Exception:
                    # One bad item_type shouldn't sink the whole search; log
                    # and move on so the other types still return.
                    logger.warning(
                        "get_items failed for item_type=%r; skipping", t, exc_info=True
                    )
                    continue
                for entry in _as_item_list(result):
                    if not isinstance(entry, dict):
                        continue
                    iid = entry.get("item_id") or entry.get("immutable_id")
                    if iid and iid in seen:
                        continue
                    if iid:
                        seen.add(iid)
                    items.append(entry)
            # Most-recent-first, so `limit` yields the newest items. (The
            # search path keeps the server's relevance order untouched.)
            items.sort(
                key=lambda it: str(it.get("last_modified") or it.get("date") or ""),
                reverse=True,
            )

        if limit:
            items = items[:limit]
        if enrich:
            items = _enrich_cells(c, items)
    finally:
        if owns:
            c.close()

    rows = [_row_from_item(it) for it in items if isinstance(it, dict)]
    return pd.DataFrame(
        rows,
        columns=[
            "item_id", "name", "refcode", "type", "chemform",
            "positive_electrode", "negative_electrode", "electrolyte",
            "last_modified", "collections", "cathode_mass_mg",
        ],
    )
