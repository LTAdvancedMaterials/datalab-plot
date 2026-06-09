"""Pure picker-grid helpers shared by the Dash GUI.

``_build_initial_df`` builds a fresh picker DataFrame from a search-results
DataFrame, optionally carrying selections / labels / groups / colors
forward by ``item_id`` from a prior frame.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from datalab_plot.plot_constants import PICKER_COLUMNS
from datalab_plot.plot_helpers import _empty_picker_df


def _build_initial_df(
    results: pd.DataFrame, prior_selected: dict[str, dict[str, Any]] | None
) -> pd.DataFrame:
    """Build a fresh initial picker DataFrame from search results.

    ``prior_selected`` (item_id → row dict) carries forward selections /
    label / group / color across new searches.
    """
    prior_selected = prior_selected or {}
    rows: list[dict[str, Any]] = []
    seen_in_results: set[str] = set()
    for _, r in results.iterrows():
        iid = r["item_id"]
        if not iid:
            continue
        seen_in_results.add(iid)
        prev = prior_selected.get(iid, {})
        mass = r.get("cathode_mass_mg")
        rows.append(
            {
                "Select": bool(prev.get("Select", False)),
                "item_id": iid,
                "name": r.get("name", "") or "",
                "positive_electrode": r.get("positive_electrode", "") or "",
                "negative_electrode": r.get("negative_electrode", "") or "",
                "electrolyte": r.get("electrolyte", "") or "",
                "cathode_mass_mg": float(mass) if mass is not None else None,
                "label": prev.get("label") or (r.get("name") or iid),
                "group": prev.get("group", "") or "",
                "color": prev.get("color", "") or "",
            }
        )
    for iid, prev in prior_selected.items():
        if iid in seen_in_results or not prev.get("Select"):
            continue
        rows.append(
            {
                "Select": True,
                "item_id": iid,
                "name": prev.get("name", "") or "",
                "positive_electrode": prev.get("positive_electrode", "") or "",
                "negative_electrode": prev.get("negative_electrode", "") or "",
                "electrolyte": prev.get("electrolyte", "") or "",
                "cathode_mass_mg": prev.get("cathode_mass_mg"),
                "label": prev.get("label") or iid,
                "group": prev.get("group", "") or "",
                "color": prev.get("color", "") or "",
            }
        )

    if not rows:
        return _empty_picker_df()
    df = pd.DataFrame(rows, columns=list(PICKER_COLUMNS))
    df["Select"] = df["Select"].astype(bool)
    return df
