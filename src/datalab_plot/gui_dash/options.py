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


def _slider(id_: str, mn: float, mx: float, step: float, value: float, **kw: Any) -> dcc.Slider:
    marks = kw.pop("marks", None)
    return dcc.Slider(
        id=id_, min=mn, max=mx, step=step, value=value,
        marks=marks if marks is not None else {},
        tooltip={"placement": "bottom", "always_visible": False},
        **kw,
    )


def layout() -> html.Div:
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
            html.Div(id="opt-summary-extras", className="mt-1"),
            # Plot options toggle — same chevron-link-button pattern as the
            # ▾ Search results / ▾ Plotting headers.
            dbc.Button(
                ["▸ ", html.Strong("Plot options")],
                id="opt-collapse-btn",
                color="link",
                size="sm",
                className="p-0 text-decoration-none text-secondary mt-2",
                title="Show / hide all plot options",
            ),
            dbc.Collapse(
                _plot_options_body(),
                id="opt-collapse",
                is_open=False,
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Button(
                            "Refresh",
                            id="opt-refresh-btn",
                            color="primary",
                            outline=True,
                            size="sm",
                            className="w-100",
                            title="Purge cache for selected items and re-fetch.",
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.Switch(
                            id="opt-auto",
                            label="Auto-refresh",
                            value=True,
                        ),
                        width="auto",
                        className="d-flex align-items-center",
                    ),
                ],
                className="g-2 mt-2 align-items-center",
            ),
            dcc.Store(id="plot-options", data={}),
        ],
    )


def _plot_options_body() -> html.Div:
    return html.Div(
        [
            html.Div("Axes & title", className="small text-muted mb-1"),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("Mode", className="small mb-1"),
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
                            dbc.Label("X", className="small mb-1"),
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
                            dbc.Label("Y (left)", className="small mb-1"),
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
                            dbc.Label("Y₂ (right)", className="small mb-1"),
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
                            dbc.Label("Title (optional)", className="small mb-1"),
                            dbc.Input(
                                id="opt-title", type="text",
                                value=_initial("ui_title"), size="sm",
                            ),
                        ],
                        width=4,
                    ),
                ],
                className="g-2",
            ),
            dbc.Switch(
                id="opt-color-by-status",
                label="Colour traces by cycler step (CC_Chg / CV_Chg / Rest …)",
                value=bool(_initial("ui_color_by_status")),
                className="mt-2",
            ),
            html.Hr(),
            html.Div("Layout & styling", className="small text-muted mb-1"),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("Plot width (%)", className="small mb-1"),
                            _slider("opt-plot-width", 40, 100, 5,
                                    int(_initial("ui_plot_width"))),
                        ],
                        width=4,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Plot height (px)", className="small mb-1"),
                            _slider("opt-plot-height", 320, 900, 20,
                                    int(_initial("ui_plot_height"))),
                        ],
                        width=4,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Trace width", className="small mb-1"),
                            _slider("opt-width-scale", 0.5, 5.0, 0.25,
                                    float(_initial("ui_width_scale"))),
                        ],
                        width=4,
                    ),
                ],
                className="g-2",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("Legend", className="small mb-1"),
                            dbc.Select(
                                id="opt-legend-mode",
                                options=_legend_options(),
                                value=str(_initial("ui_legend_mode")),
                                size="sm",
                            ),
                        ],
                        width=4,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Text size", className="small mb-1"),
                            _slider("opt-font-size", 8, 28, 1,
                                    int(_initial("ui_font_size"))),
                        ],
                        width=4,
                    ),
                    dbc.Col(
                        dbc.Switch(
                            id="opt-colorbar",
                            label="Cycle colorbar (V-vs-Q only)",
                            value=bool(_initial("ui_colorbar")),
                            className="mt-4",
                        ),
                        width=4,
                    ),
                ],
                className="g-2 mt-1",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Switch(
                            id="opt-border", label="Outer border",
                            value=bool(_initial("ui_border")),
                        ),
                        width=4,
                    ),
                    dbc.Col(
                        dbc.Switch(
                            id="opt-grid-x", label="Vertical gridlines",
                            value=bool(_initial("ui_grid_x")),
                        ),
                        width=4,
                    ),
                    dbc.Col(
                        dbc.Switch(
                            id="opt-grid-y", label="Horizontal gridlines",
                            value=bool(_initial("ui_grid_y")),
                        ),
                        width=4,
                    ),
                ],
                className="g-2 mt-1",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("Trace style", className="small mb-1"),
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
                        width=4,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Marker size", className="small mb-1"),
                            _slider("opt-marker-size", 2, 16, 1,
                                    int(_initial("ui_marker_size"))),
                        ],
                        width=4,
                    ),
                ],
                className="g-2 mt-1",
            ),
            html.Hr(),
            html.Div(
                "Axis limits — leave blank for auto:",
                className="small text-muted mb-1",
            ),
            dbc.Row(
                [
                    dbc.Col([
                        dbc.Label("x min", className="small mb-1"),
                        dbc.Input(id="opt-xmin", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("x max", className="small mb-1"),
                        dbc.Input(id="opt-xmax", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y min", className="small mb-1"),
                        dbc.Input(id="opt-ymin", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y max", className="small mb-1"),
                        dbc.Input(id="opt-ymax", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y₂ min", className="small mb-1"),
                        dbc.Input(id="opt-y2min", type="text", value="", size="sm"),
                    ], width=2),
                    dbc.Col([
                        dbc.Label("y₂ max", className="small mb-1"),
                        dbc.Input(id="opt-y2max", type="text", value="", size="sm"),
                    ], width=2),
                ],
                className="g-2",
            ),
            html.Hr(),
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
        return new_open, [glyph, html.Strong("Plot options")]

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
        Output("opt-plot-width", "value"),
        Output("opt-plot-height", "value"),
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
            return [no_update] * 25
        d = PLOT_OPTION_DEFAULTS
        preset = d["ui_preset"]
        actives = [(p == preset) for p in PRESET_OPTIONS]
        return (
            preset, actives,
            d["ui_mode"], d["ui_x_axis"], d["ui_y_axis"], d["ui_y2_axis"],
            d["ui_title"], d["ui_color_by_status"],
            d["ui_plot_width"], d["ui_plot_height"], d["ui_width_scale"],
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
        Input("opt-plot-width", "value"),
        Input("opt-plot-height", "value"),
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
    )
    def _aggregate(  # type: ignore[no-untyped-def]
        mode, x_axis, y_axis, y2_axis, title, color_by_status,
        plot_width, plot_height, width_scale,
        legend_mode, font_size, colorbar, border, grid_x, grid_y,
        marker_mode, marker_size,
        xmin, xmax, ymin, ymax, y2min, y2max,
        specific_capacity, cycle,
    ):
        return {
            "mode": mode,
            "x_axis": x_axis,
            "y_axis": y_axis,
            "y2_axis": y2_axis,
            "title": title or "",
            "color_by_status": bool(color_by_status),
            "width_frac": (int(plot_width) if plot_width else 90) / 100.0,
            "height_px": int(plot_height) if plot_height else 520,
            "width_scale": float(width_scale) if width_scale else 1.0,
            "specific_capacity": bool(specific_capacity),
            "cycle": int(cycle) if cycle else None,
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
