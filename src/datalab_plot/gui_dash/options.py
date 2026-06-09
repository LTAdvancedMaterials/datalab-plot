"""Plot-options bar for the Dash GUI: preset, axes, styling, axis limits.

The widget values are aggregated into a single ``plot-options`` Store
that the plot callback consumes, so adding/removing options only
requires touching this file.
"""
from __future__ import annotations

from typing import Any

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from datalab_plot.plot_constants import (
    AXIS_OPTIONS,
    PLOT_OPTION_DEFAULTS,
    PRESET_MAP,
    PRESET_OPTIONS,
    Y2_OPTIONS,
)
from datalab_plot.plot_helpers import _parse_limit

_MARKER_MODE_MAP: dict[str, str] = {
    "Lines": "lines",
    "Lines + points": "lines+markers",
    "Points only": "markers",
}

# Cycle Life sub-views — short button label + canonical value. The
# values match the tuple titles produced by `_plotly_summary` so the
# render-side dispatch is a dict lookup by title. "Capacity table"
# routes to the capacity-table div instead of a Plotly figure.
_SUMMARY_VIEWS: list[tuple[str, str]] = [
    ("Discharge", "Discharge capacity"),
    ("Charge",    "Charge capacity"),
    ("CE %",      "Coulombic efficiency"),
    ("Table",     "Capacity table"),
]

_PRESET_STYLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "Cycle Life": {"opt-marker-mode": "Lines + points", "opt-marker-size": 10},
}
_DEFAULT_STYLE: dict[str, Any] = {"opt-marker-mode": "Lines", "opt-marker-size": 6}


def _initial(key: str) -> Any:
    return PLOT_OPTION_DEFAULTS[key]


def _legend_options() -> list[dict[str, str]]:
    return [
        {"label": "Below the plot", "value": "below"},
        {"label": "Overlaid (top-right)", "value": "overlaid"},
        {"label": "Hidden", "value": "none"},
    ]


def preset_layout() -> html.Div:
    """Preset segmented control + inline summary-extras.

    Renders under the plot in the right column so the "what am I looking
    at" control reads as part of the visualisation.
    """
    return html.Div(
        [
            # Preset selector: built from individual dbc.Buttons in a
            # dbc.ButtonGroup so the buttons connect natively (same pattern
            # as the picker's All / None / Invert). dbc.RadioItems was
            # structurally unsuited — it always wraps each input + label in
            # a <div class="form-check">, which breaks .btn-group's
            # adjacent-sibling CSS.
            #
            # Single-select semantics are tracked in the dcc.Store below,
            # driven by `_on_preset_click`. Downstream callbacks read
            # `Input("opt-preset", "data")` instead of the old
            # `Input("opt-preset", "value")`.
            html.Div(
                dbc.ButtonGroup(
                    [
                        dbc.Button(
                            p,
                            id={"type": "opt-preset-btn", "value": p},
                            color="secondary",
                            outline=True,
                            size="sm",
                            active=(p == _initial("ui_preset")),
                            n_clicks=0,
                        )
                        for p in PRESET_OPTIONS
                    ],
                    size="sm",
                ),
                className="preset-scroller",
            ),
            dcc.Store(id="opt-preset", data=_initial("ui_preset")),
        ],
    )


def summary_view_layout() -> html.Div:
    """Cycle Life sub-view selector (Discharge / Charge / CE % / Table).

    Mirrors `preset_layout()` shape so the button row visually pairs
    with the preset row above. The container is hidden by default; the
    `_toggle_sumview` callback reveals it when `opt-preset == "Cycle
    Life"`.
    """
    initial = _SUMMARY_VIEWS[0][1]
    return html.Div(
        [
            html.Div(
                dbc.ButtonGroup(
                    [
                        dbc.Button(
                            label,
                            id={"type": "opt-sumview-btn", "value": value},
                            color="secondary",
                            outline=True,
                            size="sm",
                            active=(value == initial),
                            n_clicks=0,
                        )
                        for label, value in _SUMMARY_VIEWS
                    ],
                    size="sm",
                ),
                className="preset-scroller",
            ),
            dcc.Store(id="opt-summary-view", data=initial),
        ],
        id="opt-summary-view-row",
        style={"display": "none"},
    )


def config_layout() -> html.Div:
    """Plot options collapsible + mode-conditional extras + cache caption
    + Re-fetch / Auto-refresh row.

    Renders in the left column with the rest of the controls.
    """
    return html.Div(
        [
            dbc.Button(
                ["▸ ", "Plot options"],
                id="opt-collapse-btn",
                color="link",
                size="sm",
                className="p-0 text-decoration-none ui-section-title mb-2",
                title="Show / hide all plot options",
            ),
            dbc.Collapse(
                _plot_options_body(),
                id="opt-collapse",
                is_open=False,
                className="mb-2",
            ),
            # Mode-conditional contextual extras (Specific-capacity toggle
            # in Cycle Life mode; Cycle number input in dQ/dV mode).
            # Populated by `_render_extras` based on opt-mode.
            html.Div(id="opt-summary-extras", className="mb-2"),
            # Cache caption: "Files: X/Y cache hit · Z/Y re-downloaded."
            # Written by plotting_panel._render_plot.
            html.Div(id="plot-cache-caption", className="ui-meta mb-2"),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Button(
                            "Re-fetch",
                            id="opt-refresh-btn",
                            color="primary",
                            outline=True,
                            size="sm",
                            className="w-100",
                            title=(
                                "Re-download cell data from datalab (purges the "
                                "local file cache for staged cells). Use if you "
                                "suspect a cell's data has changed since the "
                                "last fetch."
                            ),
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.Switch(
                            id="opt-auto",
                            label="Auto-refresh",
                            value=True,
                            label_id="opt-auto-label",
                        ),
                        width="auto",
                        className="d-flex align-items-center",
                    ),
                    dbc.Tooltip(
                        "Re-render the plot automatically whenever the staged "
                        "set or options change. Doesn't re-download data — "
                        "that's what Re-fetch does.",
                        target="opt-auto-label",
                        placement="top",
                    ),
                ],
                className="g-2 align-items-center",
            ),
            dcc.Store(id="plot-options", data={}),
        ],
    )


def _plot_options_body() -> html.Div:
    # Empty-label placeholder used to vertically align a label-less Switch
    # with the label-having inputs in the same row.
    _label_spacer = dbc.Label(" ", className="ui-field-label")
    return html.Div(
        [
            html.Div("Axes & title", className="ui-subsection-label"),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("Mode", className="ui-field-label"),
                            dbc.Select(
                                id="opt-mode",
                                options=[
                                    {"label": v, "value": v}
                                    for v in ("xy", "voltage_capacity", "dqdv", "summary")
                                ],
                                value=_initial("ui_mode"),
                                size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("X", className="ui-field-label"),
                            dbc.Select(
                                id="opt-x-axis",
                                options=[{"label": v, "value": v} for v in AXIS_OPTIONS],
                                value=_initial("ui_x_axis"),
                                size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Y (left)", className="ui-field-label"),
                            dbc.Select(
                                id="opt-y-axis",
                                options=[{"label": v, "value": v} for v in AXIS_OPTIONS],
                                value=_initial("ui_y_axis"),
                                size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Y₂ (right)", className="ui-field-label"),
                            dbc.Select(
                                id="opt-y2-axis",
                                options=[{"label": v, "value": v} for v in Y2_OPTIONS],
                                value=_initial("ui_y2_axis"),
                                size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Title (optional)", className="ui-field-label"),
                            dbc.Input(
                                id="opt-title", type="text",
                                value=_initial("ui_title"), size="sm",
                            ),
                        ],
                        width=4,
                    ),
                ],
                className="g-2 mb-2",
            ),
            dbc.Switch(
                id="opt-color-by-status",
                label="Colour traces by cycler step (CC_Chg / CV_Chg / Rest …)",
                value=bool(_initial("ui_color_by_status")),
                className="mb-3",
            ),
            html.Div("Layout & styling", className="ui-subsection-label"),
            # Row 1 — numeric controls + Cycle colorbar switch.
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("X (px)", className="ui-field-label"),
                            dbc.Input(
                                id="opt-plot-w-px", type="number",
                                min=100, step=10, size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Y (px)", className="ui-field-label"),
                            dbc.Input(
                                id="opt-plot-h-px", type="number",
                                min=200, step=10, size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Text size", className="ui-field-label"),
                            dbc.Input(
                                id="opt-font-size", type="number",
                                min=8, max=28, step=1,
                                value=int(_initial("ui_font_size")),
                                size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Trace width", className="ui-field-label"),
                            dbc.Input(
                                id="opt-width-scale", type="number",
                                min=0.5, max=5.0, step=0.25,
                                value=float(_initial("ui_width_scale")),
                                size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Marker size", className="ui-field-label"),
                            dbc.Input(
                                id="opt-marker-size", type="number",
                                min=2, max=16, step=1,
                                value=int(_initial("ui_marker_size")),
                                size="sm",
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            _label_spacer,
                            dbc.Switch(
                                id="opt-colorbar",
                                label="Cycle colorbar",
                                value=bool(_initial("ui_colorbar")),
                            ),
                        ],
                        width=2,
                    ),
                ],
                className="g-2 mb-2",
            ),
            # Row 2 — selects + remaining switches.
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("Legend", className="ui-field-label"),
                            dbc.Select(
                                id="opt-legend-mode",
                                options=_legend_options(),
                                value=str(_initial("ui_legend_mode")),
                                size="sm",
                            ),
                        ],
                        width=3,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Trace style", className="ui-field-label"),
                            dbc.Select(
                                id="opt-marker-mode",
                                options=[
                                    {"label": v, "value": v}
                                    for v in _MARKER_MODE_MAP
                                ],
                                value=str(_initial("ui_marker_mode")),
                                size="sm",
                            ),
                        ],
                        width=3,
                    ),
                    dbc.Col(
                        [
                            _label_spacer,
                            dbc.Switch(
                                id="opt-border", label="Outer border",
                                value=bool(_initial("ui_border")),
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            _label_spacer,
                            dbc.Switch(
                                id="opt-grid-x", label="Vertical gridlines",
                                value=bool(_initial("ui_grid_x")),
                            ),
                        ],
                        width=2,
                    ),
                    dbc.Col(
                        [
                            _label_spacer,
                            dbc.Switch(
                                id="opt-grid-y", label="Horizontal gridlines",
                                value=bool(_initial("ui_grid_y")),
                            ),
                        ],
                        width=2,
                    ),
                ],
                className="g-2 mb-3",
            ),
            html.Div("Axis limits", className="ui-subsection-label"),
            html.Div(
                "Leave blank for auto-range.",
                className="ui-caption mb-2",
            ),
            dbc.Row(
                [
                    dbc.Col([
                        dbc.Label("x min", className="ui-field-label"),
                        dbc.Input(id="opt-xmin", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("x max", className="ui-field-label"),
                        dbc.Input(id="opt-xmax", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y min", className="ui-field-label"),
                        dbc.Input(id="opt-ymin", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y max", className="ui-field-label"),
                        dbc.Input(id="opt-ymax", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y₂ min", className="ui-field-label"),
                        dbc.Input(id="opt-y2min", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y₂ max", className="ui-field-label"),
                        dbc.Input(id="opt-y2max", type="text", value="", size="sm"),
                    ], width=2),
                ],
                className="g-2 mb-3",
            ),
            dbc.Button(
                "Reset all options to defaults",
                id="opt-reset-btn",
                color="secondary",
                outline=True,
                size="sm",
            ),
        ]
    )


def register_callbacks(app: dash.Dash) -> None:
    # --- Plot options collapse toggle (▸ ↔ ▾) ----------------------------
    @app.callback(
        Output("opt-collapse", "is_open"),
        Output("opt-collapse-btn", "children"),
        Input("opt-collapse-btn", "n_clicks"),
        State("opt-collapse", "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_options(n_clicks, is_open):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        new_open = not is_open
        glyph = "▾ " if new_open else "▸ "
        return new_open, [glyph, "Plot options"]

    # --- Preset button click → update Store + toggle active props -------
    @app.callback(
        Output("opt-preset", "data"),
        Output({"type": "opt-preset-btn", "value": ALL}, "active"),
        Input({"type": "opt-preset-btn", "value": ALL}, "n_clicks"),
        State({"type": "opt-preset-btn", "value": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _on_preset_click(_n_clicks_list, ids):  # type: ignore[no-untyped-def]
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict):
            return no_update, [no_update] * len(ids)
        selected = triggered["value"]
        actives = [(item["value"] == selected) for item in ids]
        return selected, actives

    # --- Sub-view click → update Store + toggle active props (Cycle Life)
    @app.callback(
        Output("opt-summary-view", "data"),
        Output({"type": "opt-sumview-btn", "value": ALL}, "active"),
        Input({"type": "opt-sumview-btn", "value": ALL}, "n_clicks"),
        State({"type": "opt-sumview-btn", "value": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _on_sumview_click(_clicks, ids):  # type: ignore[no-untyped-def]
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict):
            return no_update, [no_update] * len(ids)
        selected = triggered["value"]
        return selected, [(item["value"] == selected) for item in ids]

    # --- Sub-view row visibility — Cycle Life preset only ----------------
    @app.callback(
        Output("opt-summary-view-row", "style"),
        Input("opt-preset", "data"),
    )
    def _toggle_sumview(preset):  # type: ignore[no-untyped-def]
        return {} if preset == "Cycle Life" else {"display": "none"}

    # --- Preset → mode/axes (and marker defaults) ------------------------
    @app.callback(
        Output("opt-mode", "value"),
        Output("opt-x-axis", "value"),
        Output("opt-y-axis", "value"),
        Output("opt-y2-axis", "value"),
        Output("opt-marker-mode", "value", allow_duplicate=True),
        Output("opt-marker-size", "value", allow_duplicate=True),
        Input("opt-preset", "data"),
        State("opt-x-axis", "value"),
        State("opt-y-axis", "value"),
        State("opt-y2-axis", "value"),
        prevent_initial_call=True,
    )
    def _apply_preset(preset, cur_x, cur_y, cur_y2):  # type: ignore[no-untyped-def]
        if preset not in PRESET_MAP:
            return no_update, no_update, no_update, no_update, no_update, no_update
        mode, x, y, y2 = PRESET_MAP[preset]
        new_x = x if x is not None else cur_x
        new_y = y if y is not None else cur_y
        new_y2 = y2 if y2 is not None else cur_y2
        if preset != "Custom":
            style = _PRESET_STYLE_DEFAULTS.get(preset, _DEFAULT_STYLE)
            return (
                mode, new_x, new_y, new_y2,
                style["opt-marker-mode"], style["opt-marker-size"],
            )
        return mode, new_x, new_y, new_y2, no_update, no_update

    # --- Reset all options ----------------------------------------------
    @app.callback(
        Output("opt-preset", "data", allow_duplicate=True),
        Output({"type": "opt-preset-btn", "value": ALL}, "active", allow_duplicate=True),
        Output("opt-mode", "value", allow_duplicate=True),
        Output("opt-x-axis", "value", allow_duplicate=True),
        Output("opt-y-axis", "value", allow_duplicate=True),
        Output("opt-y2-axis", "value", allow_duplicate=True),
        Output("opt-title", "value"),
        Output("opt-color-by-status", "value"),
        Output("opt-width-scale", "value"),
        Output("opt-legend-mode", "value"),
        Output("opt-font-size", "value"),
        Output("opt-colorbar", "value"),
        Output("opt-border", "value"),
        Output("opt-grid-x", "value"),
        Output("opt-grid-y", "value"),
        Output("opt-marker-mode", "value", allow_duplicate=True),
        Output("opt-marker-size", "value", allow_duplicate=True),
        Output("opt-xmin", "value"),
        Output("opt-xmax", "value"),
        Output("opt-ymin", "value"),
        Output("opt-ymax", "value"),
        Output("opt-y2min", "value"),
        Output("opt-y2max", "value"),
        Input("opt-reset-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _reset_options(n_clicks):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return [no_update] * 23
        d = PLOT_OPTION_DEFAULTS
        preset = d["ui_preset"]
        actives = [(p == preset) for p in PRESET_OPTIONS]
        return (
            preset, actives,
            d["ui_mode"], d["ui_x_axis"], d["ui_y_axis"], d["ui_y2_axis"],
            d["ui_title"], d["ui_color_by_status"], d["ui_width_scale"],
            d["ui_legend_mode"], d["ui_font_size"], d["ui_colorbar"],
            d["ui_border"], d["ui_grid_x"], d["ui_grid_y"],
            d["ui_marker_mode"], d["ui_marker_size"],
            d["ui_xmin"], d["ui_xmax"], d["ui_ymin"], d["ui_ymax"],
            d["ui_y2min"], d["ui_y2max"],
        )

    # --- Inline extras: specific-capacity (summary only) + cycle (dqdv only) ---
    @app.callback(
        Output("opt-summary-extras", "children"),
        Input("opt-mode", "value"),
    )
    def _render_extras(mode):  # type: ignore[no-untyped-def]
        if mode == "summary":
            return dbc.Switch(
                id="opt-specific-capacity",
                label="Specific capacity (mAh/g) — divide by cathode mass from datalab",
                value=False,
            )
        if mode == "dqdv":
            return dbc.InputGroup(
                [
                    dbc.InputGroupText("Cycle"),
                    dbc.Input(
                        id="opt-cycle", type="number", min=1, step=1, value=1,
                        size="sm",
                    ),
                ],
                size="sm",
                style={"maxWidth": "180px"},
            )
        # Mount the IDs even when hidden so the aggregator can read them.
        return html.Div(
            [
                dbc.Switch(id="opt-specific-capacity", value=False,
                           style={"display": "none"}),
                dbc.Input(id="opt-cycle", type="number", value=1,
                          style={"display": "none"}),
            ]
        )

    # --- Aggregate every option widget into plot-options Store -----------
    @app.callback(
        Output("plot-options", "data"),
        Input("opt-mode", "value"),
        Input("opt-x-axis", "value"),
        Input("opt-y-axis", "value"),
        Input("opt-y2-axis", "value"),
        Input("opt-title", "value"),
        Input("opt-color-by-status", "value"),
        Input("opt-width-scale", "value"),
        Input("opt-legend-mode", "value"),
        Input("opt-font-size", "value"),
        Input("opt-colorbar", "value"),
        Input("opt-border", "value"),
        Input("opt-grid-x", "value"),
        Input("opt-grid-y", "value"),
        Input("opt-marker-mode", "value"),
        Input("opt-marker-size", "value"),
        Input("opt-xmin", "value"),
        Input("opt-xmax", "value"),
        Input("opt-ymin", "value"),
        Input("opt-ymax", "value"),
        Input("opt-y2min", "value"),
        Input("opt-y2max", "value"),
        Input("opt-specific-capacity", "value"),
        Input("opt-cycle", "value"),
        Input("theme", "data"),
    )
    def _aggregate(  # type: ignore[no-untyped-def]
        mode, x_axis, y_axis, y2_axis, title, color_by_status,
        width_scale,
        legend_mode, font_size, colorbar, border, grid_x, grid_y,
        marker_mode, marker_size,
        xmin, xmax, ymin, ymax, y2min, y2max,
        specific_capacity, cycle, theme,
    ):
        # Plot width/height are gone — figure dimensions come from the
        # right column's width and the .ui-plot-graph CSS height rule.
        return {
            "mode": mode,
            "x_axis": x_axis,
            "y_axis": y_axis,
            "y2_axis": y2_axis,
            "title": title or "",
            "color_by_status": bool(color_by_status),
            "width_scale": float(width_scale) if width_scale else 1.0,
            "specific_capacity": bool(specific_capacity),
            "cycle": int(cycle) if cycle else None,
            "theme": theme or "light",
            "style": {
                "border": bool(border),
                "grid_x": bool(grid_x),
                "grid_y": bool(grid_y),
                "legend_mode": legend_mode,
                "font_size": int(font_size) if font_size else 13,
                "colorbar": bool(colorbar),
                "marker_mode": _MARKER_MODE_MAP.get(marker_mode, "lines"),
                "marker_size": float(marker_size) if marker_size else 6.0,
                "x_min": _parse_limit(xmin),
                "x_max": _parse_limit(xmax),
                "y_min": _parse_limit(ymin),
                "y_max": _parse_limit(ymax),
                "y2_min": _parse_limit(y2min),
                "y2_max": _parse_limit(y2max),
            },
        }
