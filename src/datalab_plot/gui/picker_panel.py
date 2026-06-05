"""Cell picker: state management, bulk-action callbacks, and the data_editor.

Picker state uses a version-bumped key pattern.

Streamlit forbids ALL external writes to st.session_state[<data_editor key>]
(callbacks, before-first-render, ... it doesn't matter — the check fires the
next time the widget renders). So the only way to programmatically change a
data_editor's state is to make it a *different* widget. We do that by bumping
a version counter that is part of the widget's `key`.

  * picker_initial     -- the DataFrame fed into the editor. Replaced only by
                          `_set_initial`; never mutated in place.
  * picker_version     -- integer; bumped by `_set_initial`. Part of the key.
  * picker_last_edited -- the editor's return value from the previous render.
                          Bulk handlers use this to preserve the user's
                          per-row edits when constructing picker_initial.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from datalab_plot.gui.constants import PICKER_COLUMNS, PICKER_KEY_BASE
from datalab_plot.gui.helpers import _empty_picker_df


def _picker_widget_key() -> str:
    return f"{PICKER_KEY_BASE}_v{st.session_state.get('picker_version', 0)}"


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


def _set_initial(new_df: pd.DataFrame) -> None:
    """Replace the immutable initial DataFrame and bump the widget version
    so the data_editor re-mounts with the new values."""
    st.session_state["picker_initial"] = new_df.reset_index(drop=True)
    st.session_state["picker_version"] = st.session_state.get("picker_version", 0) + 1
    # The editor's return-value snapshot is also stale now.
    st.session_state.pop("picker_last_edited", None)


def _current_picker_df() -> pd.DataFrame:
    """Most recent edited frame from the previous data_editor render, or the
    initial frame if the editor hasn't rendered yet (or was just bumped)."""
    last: pd.DataFrame | None = st.session_state.get("picker_last_edited")
    if last is not None and not last.empty:
        return last.copy()
    return st.session_state.get("picker_initial", _empty_picker_df()).copy()


# --- Bulk-action callbacks. Each builds a new initial DataFrame from the
# user's current edits (so labels / groups / colours are preserved) with the
# Select column overwritten, then bumps the widget version. -----------------

def _cb_select_all() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    current["Select"] = True
    _set_initial(current)


def _cb_select_none() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    current["Select"] = False
    _set_initial(current)


def _cb_invert() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    current["Select"] = ~current["Select"].fillna(False).astype(bool)
    _set_initial(current)


def _cb_check_range() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    start = int(st.session_state.get("range_from", 1))
    end = int(st.session_state.get("range_to", len(current)))
    lo, hi = sorted((start, end))
    current = current.reset_index(drop=True)
    current.loc[current.index[lo - 1 : hi], "Select"] = True
    _set_initial(current)


def _cb_uncheck_range() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    start = int(st.session_state.get("range_from", 1))
    end = int(st.session_state.get("range_to", len(current)))
    lo, hi = sorted((start, end))
    current = current.reset_index(drop=True)
    current.loc[current.index[lo - 1 : hi], "Select"] = False
    _set_initial(current)


def _selected_payload(picker_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Build the dict[label, {item_id, group?, color?}] shape plot_cycles takes."""
    payload: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    if picker_df.empty:
        return payload
    for _, r in picker_df.iterrows():
        if not bool(r["Select"]):
            continue
        label = ((r.get("label") or "") or "").strip() or r["item_id"]
        original = label
        i = 2
        while label in seen:
            label = f"{original} ({i})"
            i += 1
        seen.add(label)
        spec: dict[str, Any] = {"item_id": r["item_id"]}
        grp = ((r.get("group") or "") or "").strip()
        col = ((r.get("color") or "") or "").strip()
        if grp:
            spec["group"] = grp
        if col:
            spec["color"] = col
        payload[label] = spec
    return payload


def _picker_table() -> pd.DataFrame:
    initial: pd.DataFrame | None = st.session_state.get("picker_initial")
    if initial is None or initial.empty:
        st.caption("Search to populate the picker. Tick the rows you want to plot.")
        return _empty_picker_df()

    # Show any per-item errors collected on the previous render.
    broken: dict[str, str] = st.session_state.get("broken_items", {})
    if broken:
        err_cols = st.columns([8, 1])
        err_cols[0].error(
            "Couldn't load these items (auto-deselected):\n"
            + "\n".join(f"• **{iid}** — {msg}" for iid, msg in broken.items())
        )
        if err_cols[1].button("Dismiss", width="stretch"):
            st.session_state["broken_items"] = {}
            st.rerun()

    # Current selection view = initial + editor diff. Used only for counts /
    # bulk-action targeting; do NOT pass it back into the data_editor's data.
    current = _current_picker_df()
    selected_now = int(current["Select"].sum())
    total = len(initial)

    # What populated the table (auto-load on connect, or a search).
    summary = st.session_state.get("results_summary")
    if summary:
        st.caption(summary)

    head = st.columns([4, 1, 1, 1])
    head[0].markdown(f"**{selected_now}** selected of {total}")
    head[1].button(
        "All", on_click=_cb_select_all, help="Select every row",
        width="stretch", disabled=total == 0,
    )
    head[2].button(
        "None", on_click=_cb_select_none, help="Clear selection",
        width="stretch", disabled=selected_now == 0,
    )
    head[3].button(
        "Invert", on_click=_cb_invert, help="Flip every checkbox",
        width="stretch", disabled=total == 0,
    )

    with st.expander("Select a range of rows", expanded=False):
        st.caption(f"Rows are 1-indexed (1–{total}).")
        rcol = st.columns([1, 1, 1, 1])
        rcol[0].number_input("From", min_value=1, max_value=total, value=1, key="range_from")
        rcol[1].number_input("To", min_value=1, max_value=total, value=total, key="range_to")
        rcol[2].button("Check range", on_click=_cb_check_range, width="stretch")
        rcol[3].button("Uncheck range", on_click=_cb_uncheck_range, width="stretch")

    # Version-bumped-key pattern: `data=` is immutable for the lifetime of a
    # given version; bulk actions / new searches build a new initial frame
    # AND bump picker_version, which makes Streamlit instantiate a fresh
    # data_editor whose initial state is the new `initial` DataFrame.
    # We never write to st.session_state[<this widget's key>] (Streamlit
    # forbids it). We read the user's per-row edits from the return value
    # and stash them for the next bulk handler.
    # Show 1-based row numbers (matching the "Select a range of rows"
    # expander's 1-indexed inputs). The display index is rebuilt every
    # render; we reset to a 0-based RangeIndex before stashing so the rest
    # of the code keeps its existing index assumptions.
    display_initial = initial.copy()
    display_initial.index = pd.RangeIndex(start=1, stop=len(display_initial) + 1, name="#")

    edited = st.data_editor(
        display_initial,
        hide_index=False,
        width="stretch",
        height=min(500, max(220, 38 * (total + 1))),
        column_config={
            # 50 px is the practical floor — glide-data-grid (the lib
            # Streamlit's data_editor wraps) hard-codes `minColumnWidth=50`
            # in the frontend bundle and silently clamps anything smaller.
            # Streamlit doesn't expose a setting to override it.
            "Select": st.column_config.CheckboxColumn("✓", width=50),
            "item_id": st.column_config.TextColumn("item_id", disabled=True, width="small"),
            "name": st.column_config.TextColumn("name", disabled=True),
            "positive_electrode": st.column_config.TextColumn(
                "+ electrode", disabled=True, help="Positive electrode constituents"
            ),
            "negative_electrode": st.column_config.TextColumn(
                "− electrode", disabled=True, help="Negative electrode constituents"
            ),
            "electrolyte": st.column_config.TextColumn(
                "electrolyte", disabled=True, help="Electrolyte constituents"
            ),
            "cathode_mass_mg": st.column_config.NumberColumn(
                "mass (mg)",
                disabled=True,
                help="Cathode mass (sum of positive-electrode constituent quantities, mg)",
                format="%.2f",
            ),
            "label": st.column_config.TextColumn("label", help="Used in the plot legend"),
            "group": st.column_config.TextColumn(
                "group", help="Same group → shared colormap"
            ),
            "color": st.column_config.TextColumn(
                "color", help="Optional colour ('#ff8800', 'C0'). Empty = auto.", width="small"
            ),
        },
        key=_picker_widget_key(),
    )
    edited = edited.reset_index(drop=True)
    edited["Select"] = edited["Select"].fillna(False).astype(bool)
    st.session_state["picker_last_edited"] = edited
    return edited
