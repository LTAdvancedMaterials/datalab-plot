"""Discover items on a datalab instance."""
from __future__ import annotations

import pandas as pd

from .client import DatalabPlotClient, _resolve_client


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
        "chemform": it.get("chemform") or "",
        "positive_electrode": _format_constituents(it.get("positive_electrode")),
        "negative_electrode": _format_constituents(it.get("negative_electrode")),
        "electrolyte": _format_constituents(it.get("electrolyte")),
        "last_modified": it.get("last_modified") or "",
        "collections": coll_names,
    }


def find_cells(
    query: str | None = None,
    *,
    item_type: str | tuple[str, ...] = ("samples", "cells"),
    limit: int | None = None,
    client: DatalabPlotClient | None = None,
) -> pd.DataFrame:
    """List items available to the authenticated user as a DataFrame.

    With ``query`` set, calls ``/search-items``. Without, calls
    ``/<item_type>`` per requested type and returns everything.

    Returned columns: ``item_id``, ``name``, ``refcode``, ``type``,
    ``chemform``, ``positive_electrode``, ``negative_electrode``,
    ``electrolyte``, ``last_modified``, ``collections``. The
    construction fields (electrodes / electrolyte) are populated
    best-effort — they appear only when the endpoint's response
    carries them.
    """
    types = (item_type,) if isinstance(item_type, str) else tuple(item_type)

    c, owns = _resolve_client(client)
    try:
        if query:
            items = c.client.search_items(query, item_types=types)
        else:
            # search_items takes multiple types in one call; get_items does not.
            items = []
            seen: set[str] = set()
            for t in types:
                try:
                    result = c.client.get_items(item_type=t)
                except Exception:
                    continue
                if not isinstance(result, list):
                    continue
                for entry in result:
                    if not isinstance(entry, dict):
                        continue
                    iid = entry.get("item_id") or entry.get("immutable_id")
                    if iid and iid in seen:
                        continue
                    if iid:
                        seen.add(iid)
                    items.append(entry)
    finally:
        if owns:
            c.close()

    if limit:
        items = items[:limit]

    rows = [_row_from_item(it) for it in items if isinstance(it, dict)]
    return pd.DataFrame(
        rows,
        columns=[
            "item_id", "name", "refcode", "type", "chemform",
            "positive_electrode", "negative_electrode", "electrolyte",
            "last_modified", "collections",
        ],
    )
