"""Search-results picker for the Dash GUI: a dash-ag-grid browse table.

Post iteration-4 the picker is strictly **browse-only**: it shows the
current search results, lets the user select rows via drag / shift-click /
Ctrl-click / All-None-Invert, and feeds an explicit "Add to plot" button.

Selection here is ephemeral — it has no direct effect on the plot.
Editing (label / group / color) happens in :mod:`datalab_plot.gui_dash.staging`
on the durable staged set, not here.
"""
from __future__ import annotations

import logging
from typing import Any

import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, ctx, html, no_update

from datalab_plot.gui_dash.state import get_state

logger = logging.getLogger(__name__)


_COLUMN_DEFS: list[dict[str, Any]] = [
    {
        "field": "item_id",
        "headerName": "item_id",
        "pinned": "left",
        "width": 130,
        "editable": False,
    },
    {"field": "name", "headerName": "name", "editable": False, "width": 180},
    {
        "field": "positive_electrode",
        "headerName": "+ electrode",
        "editable": False,
        "width": 160,
    },
    {
        "field": "negative_electrode",
        "headerName": "− electrode",
        "editable": False,
        "width": 160,
    },
    {
        "field": "electrolyte",
        "headerName": "electrolyte",
        "editable": False,
        "width": 160,
    },
    {
        "field": "cathode_mass_mg",
        "headerName": "mass (mg)",
        "editable": False,
        "type": "numericColumn",
        "valueFormatter": {"function": "params.value == null ? '' : params.value.toFixed(2)"},
        "width": 100,
    },
]

_DEFAULT_COL_DEF = {
    "resizable": True,
    "sortable": True,
    "filter": True,
    "suppressMovable": False,
}


def _df_to_rowdata(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.copy().reset_index(drop=True)
    out = out.where(pd.notna(out), None)
    return out.to_dict("records")


def _stage_from_rows(
    rows: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(merged, n_added)``: append non-duplicate ``rows`` to ``existing``.

    Dedup is by ``item_id``. Already-staged items are untouched (their
    user-tuned label/group/color is preserved). New entries get default
    ``label = name or item_id`` and empty ``group`` / ``color``.
    """
    seen_ids = {r.get("item_id") for r in existing if r.get("item_id")}
    added = 0
    out = list(existing)
    for r in rows or []:
        iid = r.get("item_id")
        if not iid or iid in seen_ids:
            continue
        seen_ids.add(iid)
        out.append({
            "item_id": iid,
            "name": r.get("name") or "",
            "label": r.get("name") or iid,
            "group": "",
            "color": "",
        })
        added += 1
    return out, added


def layout() -> html.Div:
    return html.Div(
        [
            html.Div(id="picker-broken-items", className="mb-2"),
            # Header: collapse + expand toggles, status, bulk-select, Add-to-plot
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Button(
                            ["▾ ", "Search results"],
                            id="picker-grid-collapse-btn",
                            color="link",
                            size="sm",
                            className="p-0 text-decoration-none ui-section-title",
                            title="Show / hide the search-results table",
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.Button(
                            "⤢ Expand",
                            id="picker-grid-expand-btn",
                            color="link",
                            size="sm",
                            className="p-0 text-decoration-none ui-caption",
                            title="Toggle between default and tall table",
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        html.Div(id="picker-counts", className="ui-meta"),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.ButtonGroup(
                            [
                                dbc.Button(
                                    "All", id="picker-btn-all", size="sm",
                                    color="secondary", outline=True,
                                ),
                                dbc.Button(
                                    "None", id="picker-btn-none", size="sm",
                                    color="secondary", outline=True,
                                ),
                                dbc.Button(
                                    "Invert", id="picker-btn-invert", size="sm",
                                    color="secondary", outline=True,
                                ),
                            ],
                            size="sm",
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.Button(
                            "+ Add to plot",
                            id="picker-add-btn",
                            color="primary",
                            outline=True,
                            size="sm",
                            disabled=True,
                            title="Stage the selected rows for plotting.",
                        ),
                        width="auto",
                    ),
                ],
                className="g-2 mb-2 align-items-center",
            ),
            # Grid: collapsible + expandable
            dbc.Collapse(
                html.Div(
                    dag.AgGrid(
                        id="picker-grid",
                        columnDefs=_COLUMN_DEFS,
                        rowData=[],
                        defaultColDef=_DEFAULT_COL_DEF,
                        dashGridOptions={
                            "rowSelection": {
                                "mode": "multiRow",
                                "checkboxes": True,
                                "headerCheckbox": True,
                                "enableClickSelection": True,
                                "enableSelectionWithoutKeys": False,
                            },
                            # Pin the auto-injected selection column to
                            # the very left, ahead of the pinned-left
                            # `item_id` column.
                            "selectionColumnDef": {
                                "pinned": "left",
                                "width": 40,
                                "suppressMovable": True,
                            },
                            "animateRows": False,
                            "stopEditingWhenCellsLoseFocus": True,
                            # Tint already-staged rows so dedup is visible
                            # before the user clicks Add to plot again.
                            # Colour comes from the --ag-row-staged-bg CSS
                            # variable so light/dark theme switching takes
                            # effect without a grid re-render.
                            "getRowStyle": {
                                "function": (
                                    "params.data.staged "
                                    "? {'backgroundColor': "
                                    "'var(--ag-row-staged-bg)'} : null"
                                )
                            },
                        },
                        style={"width": "100%", "height": "100%"},
                        className="ag-theme-alpine",
                        getRowId="params.data.item_id",
                    ),
                    id="picker-grid-wrapper",
                    className="picker-grid-resizer",
                ),
                id="picker-grid-collapse",
                is_open=True,
            ),
        ]
    )


def register_callbacks(app: dash.Dash) -> None:
    # --- Re-seed grid rowData on new search / connect / staging change ---
    # staging-version is an Input so the row tint updates immediately
    # after Add to plot / Remove selected without waiting for the next
    # search. The `staged` field is still set on every row — getRowStyle
    # reads it via `params.data.staged` to apply --ag-row-staged-bg.
    @app.callback(
        Output("picker-grid", "rowData"),
        Output("picker-grid", "selectedRows"),
        Input("search-version", "data"),
        Input("connection-version", "data"),
        Input("staging-version", "data"),
    )
    def _render_rows(_search_v, _conn_v, _staging_v):  # type: ignore[no-untyped-def]
        state = get_state()
        if state.get("client") is None:
            return [], []
        initial: pd.DataFrame | None = state.get("picker_initial")
        if initial is None or initial.empty:
            return [], []
        staged_ids = {
            r.get("item_id")
            for r in (state.get("staged_items") or [])
            if r.get("item_id")
        }
        row_data = _df_to_rowdata(initial)
        for r in row_data:
            r["staged"] = r.get("item_id") in staged_ids
        # Selection is ephemeral on every new search — staging is durable.
        return row_data, []

    # --- Status: "N total · M selected" + enable/disable Add ------------
    @app.callback(
        Output("picker-counts", "children"),
        Output("picker-add-btn", "disabled"),
        Output("picker-add-btn", "children"),
        Input("picker-grid", "selectedRows"),
        Input("picker-grid", "rowData"),
    )
    def _counts(selected_rows, row_data):  # type: ignore[no-untyped-def]
        n_sel = len(selected_rows or [])
        n_total = len(row_data or [])
        counts = html.Span([
            html.Strong(f"{n_total}"), " total · ",
            html.Strong(f"{n_sel}"), " selected",
        ])
        label = f"+ Add to plot ({n_sel})" if n_sel else "+ Add to plot"
        return counts, n_sel == 0, label

    # --- Bulk select: All / None / Invert --------------------------------
    @app.callback(
        Output("picker-grid", "selectedRows", allow_duplicate=True),
        Input("picker-btn-all", "n_clicks"),
        Input("picker-btn-none", "n_clicks"),
        Input("picker-btn-invert", "n_clicks"),
        State("picker-grid", "rowData"),
        State("picker-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def _bulk_select(_a, _n, _i, row_data, selected_rows):  # type: ignore[no-untyped-def]
        if not row_data:
            return no_update
        triggered = ctx.triggered_id
        if triggered == "picker-btn-all":
            return row_data
        if triggered == "picker-btn-none":
            return []
        if triggered == "picker-btn-invert":
            selected_ids = {
                r.get("item_id") for r in (selected_rows or []) if r.get("item_id")
            }
            return [r for r in row_data if r.get("item_id") not in selected_ids]
        return no_update

    # --- Add selected rows to the staged set; auto-expand Plotting ------
    @app.callback(
        Output("staging-version", "data", allow_duplicate=True),
        Output("staged-collapse", "is_open", allow_duplicate=True),
        Output("staged-collapse-btn", "children", allow_duplicate=True),
        Input("picker-add-btn", "n_clicks"),
        State("picker-grid", "selectedRows"),
        State("staging-version", "data"),
        prevent_initial_call=True,
    )
    def _add_to_plot(n_clicks, selected_rows, version):  # type: ignore[no-untyped-def]
        if not n_clicks or not selected_rows:
            return no_update, no_update, no_update
        state = get_state()
        existing = state.get("staged_items") or []
        merged, n_added = _stage_from_rows(selected_rows, existing)
        if n_added == 0:
            # All selected were already staged — no version bump needed, but
            # we still flip the Plotting section open so the user sees them.
            return no_update, True, ["▾ ", "Plotting"]
        state["staged_items"] = merged
        return (version or 0) + 1, True, ["▾ ", "Plotting"]

    # --- Collapse toggle for the grid -----------------------------------
    @app.callback(
        Output("picker-grid-collapse", "is_open"),
        Output("picker-grid-collapse-btn", "children"),
        Input("picker-grid-collapse-btn", "n_clicks"),
        State("picker-grid-collapse", "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_collapse(n_clicks, is_open):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        new_open = not is_open
        glyph = "▾ " if new_open else "▸ "
        return new_open, [glyph, "Search results"]

    # --- Expand toggle (default 400px ↔ 80vh) ---------------------------
    @app.callback(
        Output("picker-grid-wrapper", "className"),
        Output("picker-grid-expand-btn", "children"),
        Input("picker-grid-expand-btn", "n_clicks"),
        State("picker-grid-wrapper", "className"),
        prevent_initial_call=True,
    )
    def _toggle_expand(n_clicks, current_class):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        is_expanded = "expanded" in (current_class or "")
        if is_expanded:
            return "picker-grid-resizer", "⤢ Expand"
        return "picker-grid-resizer expanded", "⤡ Compact"

    # --- Broken items error banner --------------------------------------
    @app.callback(
        Output("picker-broken-items", "children"),
        Input("plot-version", "data"),
        Input("connection-version", "data"),
    )
    def _show_broken(_p, _c):  # type: ignore[no-untyped-def]
        broken: dict[str, str] = get_state().get("broken_items") or {}
        if not broken:
            return ""
        return dbc.Alert(
            [
                html.Div("Couldn't load these items (auto-deselected):"),
                html.Ul(
                    [html.Li([html.Code(iid), f" — {msg}"]) for iid, msg in broken.items()],
                    className="mb-1",
                ),
                dbc.Button(
                    "Dismiss",
                    id={"type": "picker-dismiss-broken", "index": 0},
                    size="sm",
                    color="secondary",
                    outline=True,
                ),
            ],
            color="danger",
        )

    @app.callback(
        Output("plot-version", "data", allow_duplicate=True),
        Input({"type": "picker-dismiss-broken", "index": ALL}, "n_clicks"),
        State("plot-version", "data"),
        prevent_initial_call=True,
    )
    def _dismiss_broken(n_clicks_list, plot_version):  # type: ignore[no-untyped-def]
        if not any(n_clicks_list or []):
            return no_update
        get_state()["broken_items"] = {}
        return (plot_version or 0) + 1
