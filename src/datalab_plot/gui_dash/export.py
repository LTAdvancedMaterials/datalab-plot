"""Export + save/load controls for the Dash GUI.

PNG export is served by Plotly's modebar camera button (configured via
``_PLOTLY_CONFIG`` on each ``dcc.Graph``) — no Dash code needed.

CSV export uses :mod:`datalab_plot.csv_export`.

Plot-config save/load uses :mod:`datalab_plot.gui_dash.plot_io`. A saved
JSON captures the staged set plus all plot-options widget values; loading
one restores both, triggering an Auto-refresh re-render via the existing
``picker-payload`` Store path.

**Note on restored widgets**: ``opt-specific-capacity`` and ``opt-cycle``
are stored in the JSON but NOT written back during load — these widgets
are conditionally re-mounted by ``options._render_extras`` on every mode
change, which races with writing their values. v1 documented limitation.
"""
from __future__ import annotations

import logging
from typing import Any

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, dcc, html, no_update

from datalab_plot.csv_export import figure_to_csv, figure_to_csv_tabs
from datalab_plot.gui_dash.plot_io import (
    delete_plot_config,
    list_plot_configs,
    load_plot_config,
    save_plot_config,
)
from datalab_plot.gui_dash.plotting_panel import _style_from_dict
from datalab_plot.gui_dash.state import get_state
from datalab_plot.plot_constants import PLOT_OPTION_DEFAULTS, PRESET_OPTIONS

logger = logging.getLogger(__name__)


def layout() -> html.Div:
    return html.Div(
        [
            # Row 1: PNG caption + CSV download (existing).
            dbc.Row(
                [
                    dbc.Col(
                        html.Small(
                            [
                                "PNG: hover the plot and click the camera icon. ",
                                "CSV: ",
                            ],
                            className="text-muted",
                        ),
                        width=True,
                    ),
                    dbc.Col(
                        dbc.Button(
                            "Download CSV",
                            id="export-csv-btn",
                            color="secondary",
                            outline=True,
                            size="sm",
                        ),
                        width="auto",
                    ),
                ],
                className="g-2 align-items-center mb-2",
            ),
            # Row 2: Save current plot.
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Label(
                            "Save current plot:",
                            html_for="export-save-name",
                            className="small mb-0",
                        ),
                        width="auto",
                        className="d-flex align-items-center",
                    ),
                    dbc.Col(
                        dbc.Input(
                            id="export-save-name",
                            type="text",
                            size="sm",
                            placeholder="config name (e.g. team-A-week-12)",
                        ),
                        width=4,
                    ),
                    dbc.Col(
                        dbc.Button(
                            "Save",
                            id="export-save-btn",
                            color="primary",
                            outline=True,
                            size="sm",
                            disabled=True,
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        html.Div(id="export-save-feedback", className="small"),
                        width=True,
                    ),
                ],
                className="g-2 align-items-center mb-2",
            ),
            # Row 3: Load saved plot.
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Label(
                            "Load saved plot:",
                            html_for="export-load-select",
                            className="small mb-0",
                        ),
                        width="auto",
                        className="d-flex align-items-center",
                    ),
                    dbc.Col(
                        dbc.Select(
                            id="export-load-select",
                            options=[],
                            value=None,
                            size="sm",
                        ),
                        width=4,
                    ),
                    dbc.Col(
                        dbc.Button(
                            "Load",
                            id="export-load-btn",
                            color="secondary",
                            outline=True,
                            size="sm",
                            disabled=True,
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        dbc.Button(
                            "✕",
                            id="export-delete-btn",
                            color="secondary",
                            outline=True,
                            size="sm",
                            disabled=True,
                            title="Delete the selected saved plot",
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        html.Div(id="export-load-feedback", className="small"),
                        width=True,
                    ),
                ],
                className="g-2 align-items-center",
            ),
            dcc.Download(id="export-csv-download"),
        ]
    )


# Helpers ---------------------------------------------------------------

def _select_options() -> list[dict[str, str]]:
    """Return the options list for the Load Select, sorted newest-first."""
    return [
        {"label": f"{e['name']} · {e['n_items']} cells", "value": e["stem"]}
        for e in list_plot_configs()
    ]


def register_callbacks(app: dash.Dash) -> None:
    # --- CSV download (unchanged) ---------------------------------------
    @app.callback(
        Output("export-csv-download", "data"),
        Input("export-csv-btn", "n_clicks"),
        State("plot-options", "data"),
        prevent_initial_call=True,
    )
    def _download(n_clicks, options):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update
        state = get_state()
        fig_or_tabs = state.get("last_fig")
        if fig_or_tabs is None:
            return no_update
        style = _style_from_dict((options or {}).get("style"))
        try:
            if isinstance(fig_or_tabs, list):
                csv = figure_to_csv_tabs(fig_or_tabs, style)
            else:
                csv = figure_to_csv(fig_or_tabs, style)
        except Exception:
            logger.warning("CSV export failed", exc_info=True)
            return no_update
        if not csv:
            return no_update
        return {
            "content": csv,
            "filename": "datalab_plot.csv",
            "type": "text/csv",
        }

    # --- Save button disabled when name input is empty -------------------
    @app.callback(
        Output("export-save-btn", "disabled"),
        Input("export-save-name", "value"),
    )
    def _save_btn_disabled(name):  # type: ignore[no-untyped-def]
        return not (name and name.strip())

    # --- Load + Delete buttons disabled when no selection ----------------
    @app.callback(
        Output("export-load-btn", "disabled"),
        Output("export-delete-btn", "disabled"),
        Input("export-load-select", "value"),
    )
    def _load_btn_disabled(stem):  # type: ignore[no-untyped-def]
        disabled = not stem
        return disabled, disabled

    # --- Save current plot config ----------------------------------------
    @app.callback(
        Output("save-version", "data", allow_duplicate=True),
        Output("export-save-name", "value"),
        Output("export-save-feedback", "children"),
        Input("export-save-btn", "n_clicks"),
        State("export-save-name", "value"),
        State("plot-options", "data"),
        State("opt-preset", "data"),
        State("save-version", "data"),
        prevent_initial_call=True,
    )
    def _save(n_clicks, name, options, preset, version):  # type: ignore[no-untyped-def]
        if not n_clicks or not (name and name.strip()):
            return no_update, no_update, no_update
        state = get_state()
        client = state.get("client")
        staged_items = list(state.get("staged_items") or [])
        config: dict[str, Any] = {
            "datalab_url": client.client.datalab_api_url if client else "",
            "staged_items": staged_items,
            "preset": preset,
            "options": options or {},
        }
        try:
            path = save_plot_config(name, config)
        except ValueError as exc:
            return no_update, no_update, html.Span(
                f"Save failed: {exc}", className="text-danger"
            )
        except OSError:
            logger.warning("Could not write saved plot file", exc_info=True)
            return no_update, no_update, html.Span(
                "Save failed (disk error)", className="text-danger"
            )
        return (
            (version or 0) + 1,
            "",
            html.Span(f"Saved to {path.name}", className="text-success"),
        )

    # --- Refresh the Load select options whenever a save/delete happens
    # or a fresh connection is established. ------------------------------
    @app.callback(
        Output("export-load-select", "options"),
        Input("save-version", "data"),
        Input("connection-version", "data"),
    )
    def _refresh_load_options(_sv, _cv):  # type: ignore[no-untyped-def]
        return _select_options()

    # --- Delete the selected saved plot ----------------------------------
    @app.callback(
        Output("save-version", "data", allow_duplicate=True),
        Output("export-load-select", "value"),
        Output("export-load-feedback", "children", allow_duplicate=True),
        Input("export-delete-btn", "n_clicks"),
        State("export-load-select", "value"),
        State("save-version", "data"),
        prevent_initial_call=True,
    )
    def _delete(n_clicks, stem, version):  # type: ignore[no-untyped-def]
        if not n_clicks or not stem:
            return no_update, no_update, no_update
        try:
            delete_plot_config(stem)
        except OSError:
            logger.warning("Could not delete saved plot", exc_info=True)
            return no_update, no_update, html.Span(
                "Delete failed", className="text-danger"
            )
        return (
            (version or 0) + 1,
            None,
            html.Span(f"Deleted {stem}", className="text-muted"),
        )

    # --- Load a saved plot config ----------------------------------------
    # Restores staged_items into state + writes every option widget value.
    # The preset Store, the preset-button actives, and every option widget
    # all use allow_duplicate=True because they're also written by other
    # callbacks (_apply_preset, _on_preset_click, _reset_options).
    @app.callback(
        Output("staging-version", "data", allow_duplicate=True),
        Output("opt-preset", "data", allow_duplicate=True),
        Output({"type": "opt-preset-btn", "value": ALL}, "active", allow_duplicate=True),
        Output("opt-mode", "value", allow_duplicate=True),
        Output("opt-x-axis", "value", allow_duplicate=True),
        Output("opt-y-axis", "value", allow_duplicate=True),
        Output("opt-y2-axis", "value", allow_duplicate=True),
        Output("opt-title", "value", allow_duplicate=True),
        Output("opt-color-by-status", "value", allow_duplicate=True),
        Output("opt-plot-width", "value", allow_duplicate=True),
        Output("opt-plot-height", "value", allow_duplicate=True),
        Output("opt-width-scale", "value", allow_duplicate=True),
        Output("opt-legend-mode", "value", allow_duplicate=True),
        Output("opt-font-size", "value", allow_duplicate=True),
        Output("opt-colorbar", "value", allow_duplicate=True),
        Output("opt-border", "value", allow_duplicate=True),
        Output("opt-grid-x", "value", allow_duplicate=True),
        Output("opt-grid-y", "value", allow_duplicate=True),
        Output("opt-marker-mode", "value", allow_duplicate=True),
        Output("opt-marker-size", "value", allow_duplicate=True),
        Output("opt-xmin", "value", allow_duplicate=True),
        Output("opt-xmax", "value", allow_duplicate=True),
        Output("opt-ymin", "value", allow_duplicate=True),
        Output("opt-ymax", "value", allow_duplicate=True),
        Output("opt-y2min", "value", allow_duplicate=True),
        Output("opt-y2max", "value", allow_duplicate=True),
        Output("export-load-feedback", "children", allow_duplicate=True),
        Input("export-load-btn", "n_clicks"),
        State("export-load-select", "value"),
        State("staging-version", "data"),
        prevent_initial_call=True,
    )
    def _load(  # type: ignore[no-untyped-def]
        n_clicks, stem, staging_version,
    ):
        if not n_clicks or not stem:
            return [no_update] * 27
        try:
            cfg = load_plot_config(stem)
        except FileNotFoundError:
            return [no_update] * 26 + [
                html.Span("Saved plot not found", className="text-danger"),
            ]
        except (ValueError, OSError):
            logger.warning("Could not load saved plot", exc_info=True)
            return [no_update] * 26 + [
                html.Span("Load failed (corrupt file?)", className="text-danger"),
            ]

        # 1. Restore staged_items in state + bump staging-version.
        staged = cfg.get("staged_items") or []
        get_state()["staged_items"] = list(staged)

        # 2. Restore preset + button actives.
        d = PLOT_OPTION_DEFAULTS
        preset = cfg.get("preset") or d["ui_preset"]
        actives = [(p == preset) for p in PRESET_OPTIONS]

        # 3. Restore option widget values. Fall back to defaults for any
        # missing key (saved JSONs from a future schema may be missing
        # keys; old JSONs missing keys we've since added).
        options: dict[str, Any] = cfg.get("options") or {}
        style: dict[str, Any] = options.get("style") or {}

        def opt(key: str, default_key: str) -> Any:
            v = options.get(key)
            return v if v is not None else d[default_key]

        def sty(key: str, default_key: str) -> Any:
            v = style.get(key)
            return v if v is not None else d[default_key]

        # Marker mode in the Store is the plotly-mode string; the widget
        # value is the display label. Invert via PLOT_OPTION_DEFAULTS keys.
        _PLOTLY_TO_LABEL = {
            "lines": "Lines",
            "lines+markers": "Lines + points",
            "markers": "Points only",
        }
        marker_mode_label = _PLOTLY_TO_LABEL.get(
            style.get("marker_mode") or "lines", d["ui_marker_mode"]
        )

        # Axis-limit fields are strings in the widget (blank == auto).
        def lim_str(v: Any) -> str:
            return "" if v is None else str(v)

        return (
            (staging_version or 0) + 1,
            preset, actives,
            opt("mode", "ui_mode"),
            opt("x_axis", "ui_x_axis"),
            opt("y_axis", "ui_y_axis"),
            opt("y2_axis", "ui_y2_axis"),
            opt("title", "ui_title"),
            bool(options.get("color_by_status", d["ui_color_by_status"])),
            int(round((options.get("width_frac") or d["ui_plot_width"] / 100.0) * 100)),
            int(options.get("height_px") or d["ui_plot_height"]),
            float(options.get("width_scale") or d["ui_width_scale"]),
            sty("legend_mode", "ui_legend_mode"),
            int(sty("font_size", "ui_font_size")),
            bool(sty("colorbar", "ui_colorbar")),
            bool(sty("border", "ui_border")),
            bool(sty("grid_x", "ui_grid_x")),
            bool(sty("grid_y", "ui_grid_y")),
            marker_mode_label,
            int(sty("marker_size", "ui_marker_size")),
            lim_str(style.get("x_min")),
            lim_str(style.get("x_max")),
            lim_str(style.get("y_min")),
            lim_str(style.get("y_max")),
            lim_str(style.get("y2_min")),
            lim_str(style.get("y2_max")),
            html.Span(
                f"Loaded {cfg.get('name', stem)} · {len(staged)} cells",
                className="text-success",
            ),
        )
