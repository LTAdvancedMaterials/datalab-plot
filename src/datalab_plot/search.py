"""Discover items on a datalab instance."""
from __future__ import annotations

import pandas as pd

from .client import DatalabPlotClient, _resolve_client


def find_cells(
    query: str | None = None,
    *,
    item_type: str | tuple[str, ...] = ("samples", "cells"),
    limit: int | None = None,
    client: DatalabPlotClient | None = None,
) -> pd.DataFrame:
    """List items available to the authenticated user as a DataFrame.

    With ``query`` set, calls ``/search-items``. Without, calls ``/<item_type>``
    and returns everything.

    Returned columns: ``item_id``, ``name``, ``refcode``, ``type``, ``chemform``,
    ``last_modified``, ``collections``. Missing fields are filled with empty
    strings.
    """
    c, owns = _resolve_client(client)
    try:
        if query:
            types = (item_type,) if isinstance(item_type, str) else tuple(item_type)
            items = c.client.search_items(query, item_types=types)
        else:
            # search_items supports multiple types in one call, get_items does not.
            types = (item_type,) if isinstance(item_type, str) else tuple(item_type)
            items = []
            seen_ids: set[str] = set()
            for t in types:
                try:
                    result = c.client.get_items(item_type=t)
                except Exception:
                    # Some endpoints (e.g. 'cells' alias) might not be available; ignore.
                    continue
                if not isinstance(result, list):
                    # Some server responses come back as dicts when the endpoint
                    # didn't match the expected shape; skip rather than treating
                    # the dict's keys as item records.
                    continue
                for entry in result:
                    if not isinstance(entry, dict):
                        continue
                    iid = entry.get("item_id") or entry.get("immutable_id")
                    if iid and iid in seen_ids:
                        continue
                    if iid:
                        seen_ids.add(iid)
                    items.append(entry)
    finally:
        if owns:
            c.close()

    if limit:
        items = items[:limit]

    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        collections = it.get("collections") or []
        coll_names = ", ".join(
            (c.get("collection_id") or c.get("immutable_id") or "") for c in collections
        )
        rows.append(
            {
                "item_id": it.get("item_id") or "",
                "name": it.get("name") or "",
                "refcode": it.get("refcode") or "",
                "type": it.get("type") or "",
                "chemform": it.get("chemform") or "",
                "last_modified": it.get("last_modified") or "",
                "collections": coll_names,
            }
        )
    return pd.DataFrame(rows)
