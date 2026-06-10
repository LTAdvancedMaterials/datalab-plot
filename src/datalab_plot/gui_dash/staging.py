"""Staged-set ("Plotting") panel for the Dash GUI.

The staged set is the durable list of cells that drive the plot. It lives
in ``state["staged_items"]`` and survives across new searches — adding
rows from a second search appends to the staged set, doesn't replace it.

This module owns:
  * The staged grid (AG Grid with editable label/group/color).
  * The Apply-to-selection toolbar (Group / Color / Label inputs + Apply).
  * The Remove-selected button.
  * Emitting the ``picker-payload`` Store whenever ``staged_items`` changes,
    so the plot callback (which still consumes ``picker-payload``) refreshes.

``picker.py`` is now strictly "browse search results"; this module is where
"choose what to plot" happens.
"""
from __future__ import annotations

import logging
from typing import Any

import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import Input, Output, State, ctx, html, no_update

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
    {"field": "name", "headerName": "name", "editable": False, "width": 200},
    {
        "field": "label",
        "headerName": "label",
        "editable": True,
        "width": 200,
        "cellStyle": {"backgroundColor": "var(--ag-editable-cell-bg)"},
    },
    {
        "field": "group",
        "headerName": "group",
        "editable": True,
        "width": 130,
        "cellStyle": {"backgroundColor": "var(--ag-editable-cell-bg)"},
    },
    {
        "field": "color",
        "headerName": "color",
        "editable": True,
        "width": 130,
        "cellStyle": {"backgroundColor": "var(--ag-editable-cell-bg)"},
    },
]

_DEFAULT_COL_DEF = {
    "resizable": True,
    "sortable": True,
    "filter": True,
    "suppressMovable": False,
}

# Editable columns that the apply-toolbar can target.
_APPLY_COLS = ("group", "color", "label")


def build_payload(staged: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build the ``{label → spec}`` payload from the staged items list.

    Same shape ``plotting.build_figure_for_payload`` expects (and that
    the previous picker callback emitted): keys are unique labels;
    values carry ``item_id`` + optional ``group`` / ``color``.
    """
    payload: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for r in staged or []:
        iid = r.get("item_id")
        if not iid:
            continue
        label = ((r.get("label") or "") or "").strip() or iid
        original = label
        i = 2
        while label in seen:
            label = f"{original} ({i})"
            i += 1
        seen.add(label)
        spec: dict[str, Any] = {"item_id": iid}
        grp = ((r.get("group") or "") or "").strip()
        col = ((r.get("color") or "") or "").strip()
        if grp:
            spec["group"] = grp
        if col:
            spec["color"] = col
        payload[label] = spec
    return payload


def _apply_value(
    col: str, value: str | None,
    staged: list[dict[str, Any]],
    selected_ids: set[str],
) -> list[dict[str, Any]]:
    """Return a new staged list with ``col`` set to ``value`` for selected rows."""
    value = "" if value is None else str(value).strip()
    out: list[dict[str, Any]] = []
    for r in staged:
        r2 = dict(r)
        if r2.get("item_id") in selected_ids:
            r2[col] = value
        out.append(r2)
    return out


def _apply_field(label: str, input_id: str, btn_id: str) -> dbc.Row:
    """One labelled (input + Apply) cluster for the Apply-to-selection bar."""
    return dbc.Row(
        [
            dbc.Col(
                dbc.Label(label, html_for=input_id, className="ui-field-label mb-0"),
                width="auto",
                className="d-flex align-items-center pe-1",
            ),
            dbc.Col(
                dbc.Input(
                    id=input_id,
                    type="text",
                    size="sm",
                    debounce=False,
                    n_submit=0,
                    placeholder=f"new {label.lower()} value",
                    style={"minWidth": "8rem"},
                ),
                width="auto",
            ),
            dbc.Col(
                dbc.Button(
                    "Apply",
                    id=btn_id,
                    color="primary",
                    outline=True,
                    size="sm",
                ),
                width="auto",
                className="ps-1",
            ),
        ],
        className="g-2 me-3 align-items-center flex-nowrap",
    )


def layout() -> html.Div:
    return html.Div(
        [
            # Header: collapse/expand toggles, status, Remove
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Button(
                            ["▸ ", "Plotting"],
                            id="staged-collapse-btn",
                            color="link",
                            size="sm",
                            className="p-0 text-decoration-none ui-section-title",
                            title="Show / hide the staged-items table",
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.Button(
                            "⤢ Expand",
                            id="staged-expand-btn",
                            color="link",
                            size="sm",
                            className="p-0 text-decoration-none ui-caption",
                            title="Toggle between default and tall table",
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        html.Div(id="staged-counts", className="ui-meta"),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.Button(
                            "Remove selected",
                            id="staged-remove-btn",
                            color="secondary",
                            outline=True,
                            size="sm",
                            disabled=True,
                        ),
                        width="auto",
                    ),
                ],
                className="g-2 mb-2 align-items-center",
            ),
            # Grid + apply-toolbar collapse together under the Plotting
            # section header. Apply-toolbar lives inside the Collapse so
            # the section header is the single show/hide control.
            dbc.Collapse(
                [
                    # Apply-to-selection toolbar (targets staged-grid
                    # selection). The Group / Color / Label input cluster
                    # lives in an inner Div whose `display: none` toggles
                    # off when no rows are selected — see
                    # `_selection_state`. Only the prompt text stays
                    # visible in that case.
                    html.Div(
                        dbc.Row(
                            [
                                dbc.Col(
                                    html.Span(
                                        id="staged-apply-prompt",
                                        className="ui-subsection-label mb-0",
                                    ),
                                    width="auto",
                                    className="d-flex align-items-center pe-2",
                                ),
                                dbc.Col(
                                    html.Div(
                                        [
                                            html.Div(
                                                _apply_field(
                                                    "Group",
                                                    "staged-apply-group-input",
                                                    "staged-apply-group-btn",
                                                ),
                                            ),
                                            html.Div(
                                                _apply_field(
                                                    "Color",
                                                    "staged-apply-color-input",
                                                    "staged-apply-color-btn",
                                                ),
                                            ),
                                            html.Div(
                                                _apply_field(
                                                    "Label",
                                                    "staged-apply-label-input",
                                                    "staged-apply-label-btn",
                                                ),
                                            ),
                                        ],
                                        id="staged-apply-fields",
                                        className=(
                                            "d-flex gap-2 flex-wrap "
                                            "align-items-center"
                                        ),
                                    ),
                                    width=True,
                                ),
                            ],
                            className="g-2 align-items-center flex-wrap",
                        ),
                        className="apply-toolbar mb-2",
                    ),
                    html.Div(
                        dag.AgGrid(
                            id="staged-grid",
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
                                # Pin the auto-injected selection column
                                # to the very left, ahead of pinned-left
                                # user columns.
                                "selectionColumnDef": {
                                    "pinned": "left",
                                    "width": 40,
                                    "suppressMovable": True,
                                },
                                "animateRows": False,
                                "stopEditingWhenCellsLoseFocus": True,
                            },
                            style={"width": "100%", "height": "100%"},
                            className="ag-theme-alpine",
                            getRowId="params.data.item_id",
                        ),
                        id="staged-grid-wrapper",
                        className="picker-grid-resizer",
                    ),
                ],
                id="staged-collapse",
                is_open=False,
            ),
        ]
    )


def register_callbacks(app: dash.Dash) -> None:
    # --- Re-render staged grid + emit picker-payload + counts ------------
    @app.callback(
        Output("staged-grid", "rowData"),
        Output("picker-payload", "data", allow_duplicate=True),
        Output("staged-counts", "children"),
        Input("staging-version", "data"),
        Input("connection-version", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def _render_staged(_v, _c):  # type: ignore[no-untyped-def]
        state = get_state()
        if state.get("client") is None:
            return [], {}, ""
        staged: list[dict[str, Any]] = state.get("staged_items") or []
        payload = build_payload(staged)
        n = len(staged)
        counts = html.Span([html.Strong(f"{n}"), " staged"])
        return staged, payload, counts

    # --- Update apply-toolbar prompt + Remove-btn disabled on selection --
    # Also toggles `staged-apply-fields.style` so the Group / Color /
    # Label inputs only appear when there's a selection to act on.
    @app.callback(
        Output("staged-apply-prompt", "children"),
        Output("staged-apply-fields", "style"),
        Output("staged-apply-group-btn", "disabled"),
        Output("staged-apply-color-btn", "disabled"),
        Output("staged-apply-label-btn", "disabled"),
        Output("staged-remove-btn", "disabled"),
        Input("staged-grid", "selectedRows"),
        prevent_initial_call=False,
    )
    def _selection_state(selected_rows):  # type: ignore[no-untyped-def]
        n = len(selected_rows or [])
        if n == 0:
            prompt = "Apply to selection — pick rows first:"
        elif n == 1:
            prompt = "Apply to 1 staged row:"
        else:
            prompt = f"Apply to {n} staged rows:"
        disabled = n == 0
        fields_style = {"display": "none"} if n == 0 else {}
        return prompt, fields_style, disabled, disabled, disabled, disabled

    # --- Apply group / color / label to selected staged rows -------------
    @app.callback(
        Output("staging-version", "data", allow_duplicate=True),
        Output("staged-apply-group-input", "value"),
        Output("staged-apply-color-input", "value"),
        Output("staged-apply-label-input", "value"),
        Input("staged-apply-group-btn", "n_clicks"),
        Input("staged-apply-color-btn", "n_clicks"),
        Input("staged-apply-label-btn", "n_clicks"),
        Input("staged-apply-group-input", "n_submit"),
        Input("staged-apply-color-input", "n_submit"),
        Input("staged-apply-label-input", "n_submit"),
        State("staged-apply-group-input", "value"),
        State("staged-apply-color-input", "value"),
        State("staged-apply-label-input", "value"),
        State("staged-grid", "selectedRows"),
        State("staging-version", "data"),
        prevent_initial_call=True,
    )
    def _apply_to_selection(  # type: ignore[no-untyped-def]
        _g, _c, _l, _gs, _cs, _ls,
        v_group, v_color, v_label,
        selected_rows, version,
    ):
        if not selected_rows:
            return no_update, no_update, no_update, no_update
        triggered = ctx.triggered_id
        col_value_for = {
            "staged-apply-group-btn":   ("group", v_group),
            "staged-apply-color-btn":   ("color", v_color),
            "staged-apply-label-btn":   ("label", v_label),
            "staged-apply-group-input": ("group", v_group),
            "staged-apply-color-input": ("color", v_color),
            "staged-apply-label-input": ("label", v_label),
        }
        if triggered not in col_value_for:
            return no_update, no_update, no_update, no_update
        col, value = col_value_for[triggered]
        selected_ids = {r.get("item_id") for r in selected_rows if r.get("item_id")}
        state = get_state()
        staged = state.get("staged_items") or []
        state["staged_items"] = _apply_value(col, value, staged, selected_ids)
        # Clear only the input that was just applied.
        return (
            (version or 0) + 1,
            "" if col == "group" else no_update,
            "" if col == "color" else no_update,
            "" if col == "label" else no_update,
        )

    # --- Cell edits on the staged grid → persist into state --------------
    @app.callback(
        Output("staging-version", "data", allow_duplicate=True),
        Input("staged-grid", "cellValueChanged"),
        State("staged-grid", "rowData"),
        State("staging-version", "data"),
        prevent_initial_call=True,
    )
    def _on_cell_edit(cell_changed, row_data, version):  # type: ignore[no-untyped-def]
        if not cell_changed or not row_data:
            return no_update
        # rowData is post-edit. Persist verbatim — staged_items mirrors it.
        get_state()["staged_items"] = list(row_data)
        return (version or 0) + 1

    # --- Remove selected from staged set ---------------------------------
    @app.callback(
        Output("staging-version", "data", allow_duplicate=True),
        Output("staged-grid", "selectedRows", allow_duplicate=True),
        Input("staged-remove-btn", "n_clicks"),
        State("staged-grid", "selectedRows"),
        State("staging-version", "data"),
        prevent_initial_call=True,
    )
    def _remove_selected(n_clicks, selected_rows, version):  # type: ignore[no-untyped-def]
        if not n_clicks or not selected_rows:
            return no_update, no_update
        remove_ids = {r.get("item_id") for r in selected_rows if r.get("item_id")}
        state = get_state()
        staged = state.get("staged_items") or []
        state["staged_items"] = [r for r in staged if r.get("item_id") not in remove_ids]
        return (version or 0) + 1, []

    # --- Collapse toggle -------------------------------------------------
    @app.callback(
        Output("staged-collapse", "is_open"),
        Output("staged-collapse-btn", "children"),
        Input("staged-collapse-btn", "n_clicks"),
        State("staged-collapse", "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_collapse(n_clicks, is_open):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        new_open = not is_open
        glyph = "▾ " if new_open else "▸ "
        return new_open, [glyph, "Plotting"]

    # --- Expand toggle (default 400px ↔ 80vh) ---------------------------
    @app.callback(
        Output("staged-grid-wrapper", "className"),
        Output("staged-expand-btn", "children"),
        Input("staged-expand-btn", "n_clicks"),
        State("staged-grid-wrapper", "className"),
        prevent_initial_call=True,
    )
    def _toggle_expand(n_clicks, current_class):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        is_expanded = "expanded" in (current_class or "")
        if is_expanded:
            return "picker-grid-resizer", "⤢ Expand"
        return "picker-grid-resizer expanded", "⤡ Compact"
