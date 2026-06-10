"""Main plot area for the Dash GUI.

Owns the ``main-plot`` ``dcc.Graph`` (single-fig modes) and the
``tabs-plot-container`` Div (Cycle Life mode), the plot-trigger logic
(Auto-refresh + Refresh button), and the cache-stats caption beneath
the plot.

**Zoom persistence**: the ``dcc.Graph(id="main-plot")`` is mounted at
``layout()`` time and never replaced — the plot callback writes only to
its ``figure`` prop. Combined with ``layout.uirevision`` keyed on the
mode + axis configuration, Plotly preserves UI state (zoom, pan, legend
toggles) across styling changes.

**Vertical stability**: ``dcc.Loading`` is used in **overlay** mode
(``overlay_style={"visibility": "visible", ...}`` + ``target_components
={"main-plot": "figure", "tabs-plot-container": "children"}``) so the
Graph stays mounted under the spinner — the default child-swap mode
would collapse the height and drop uirevision. A fixed-height warnings
container keeps appearing/disappearing alerts from reflowing the page.
"""
from __future__ import annotations

import logging
from typing import Any

import dash
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html, no_update

from datalab_plot.gui_dash.state import get_state
from datalab_plot.plot_constants import PlotStyle
from datalab_plot.plotly_builders import (
    _PLOTLY_CONFIG,
    _build_capacity_table,
    build_figure_for_payload,
)

logger = logging.getLogger(__name__)


def _style_from_dict(d: dict[str, Any] | None) -> PlotStyle:
    """Build a PlotStyle from the plot-options Store's style sub-dict."""
    d = d or {}
    return PlotStyle(
        border=bool(d.get("border", True)),
        grid_x=bool(d.get("grid_x", True)),
        grid_y=bool(d.get("grid_y", True)),
        legend_mode=str(d.get("legend_mode", "below")),
        font_size=int(d.get("font_size", 13)),
        colorbar=bool(d.get("colorbar", False)),
        marker_mode=str(d.get("marker_mode", "lines")),
        marker_size=float(d.get("marker_size", 6.0)),
        x_min=d.get("x_min"),
        x_max=d.get("x_max"),
        y_min=d.get("y_min"),
        y_max=d.get("y_max"),
        y2_min=d.get("y2_min"),
        y2_max=d.get("y2_max"),
    )


def _ui_revision(mode: str, x: str, y: str, y2: str, cycle: int | None) -> str:
    """Stable uirevision within a mode/axes/cycle combo.

    Same revision across pure styling changes (font, marker, gridlines)
    keeps Plotly's UI state (zoom, pan, legend toggles); different revision
    on mode/axis change resets zoom — the user is asking for a different
    plot.
    """
    return f"{mode}|{x}|{y}|{y2}|{cycle if cycle is not None else ''}"


def _empty_figure(message: str = "", theme: str = "light") -> go.Figure:
    """Blank-canvas placeholder figure.

    When ``message`` is empty the figure renders with no annotation —
    the dcc.Loading overlay's spinner then sits cleanly on a blank
    backdrop rather than partially hiding text. Educational messaging
    for the empty state lives in the onboarding hint above the plot
    panel; reserve ``message`` for informative states only ("Not
    connected.", "All staged cells are missing cycling data…").
    """
    template = "plotly_dark" if theme == "dark" else "plotly_white"
    text_color = "#bbb" if theme == "dark" else "#888"
    fig = go.Figure()
    if message:
        fig.add_annotation(
            text=message, xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=16, color=text_color),
        )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    # No `height=` — let the figure inherit the container's CSS
    # --plot-height (see .ui-plot-graph rule). Hardcoding here would
    # shrink the placeholder to a fixed pixel height.
    fig.update_layout(
        template=template, margin=dict(l=20, r=20, t=20, b=20),
    )
    return fig


def _wrap_uirev(fig: go.Figure, revision: str) -> go.Figure:
    fig.update_layout(uirevision=revision)
    return fig


def _capacity_table_layout(
    cycle_summaries: dict[str, pd.DataFrame],
    payload: dict[str, dict[str, Any]],
) -> html.Div:
    item_ids = {label: spec.get("item_id", "") for label, spec in payload.items()}
    table = _build_capacity_table(cycle_summaries, 2, item_ids)
    return html.Div(
        [
            dbc.InputGroup(
                [
                    dbc.InputGroupText("Cycle"),
                    dbc.Input(
                        id="capacity-table-cycle",
                        type="number", min=1, step=1, value=2, size="sm",
                    ),
                ],
                size="sm",
                className="mb-2",
                style={"maxWidth": "180px"},
            ),
            dbc.Table.from_dataframe(
                table, striped=True, bordered=True, hover=True, size="sm",
                id="capacity-table",
            ),
        ]
    )


def layout() -> html.Div:
    return html.Div(
        [
            # Onboarding hint: visible when connected AND staged set is empty.
            # Vanishes after the first Add to plot. See _toggle_onboarding_hint.
            html.Div(
                dbc.Alert(
                    [
                        html.Strong("Get started: "),
                        "search for cells, select rows in ",
                        html.Strong("Search results"),
                        ", then click ",
                        html.Strong("+ Add to plot"),
                        ". Your plot appears here.",
                    ],
                    color="info",
                    className="mb-0",
                ),
                id="onboarding-hint",
                className="mb-2",
            ),
            # The main Graph is mounted ONCE here and never replaced —
            # only its `figure` prop is written by the plot callback.
            # This is what makes uirevision actually preserve zoom
            # across styling changes. Plot wrapper carries a CSS
            # variable --plot-height which the horizontal divider
            # updates on drag, read via the .ui-plot-graph rule's
            # `height: var(--plot-height)`.
            html.Div(
                [
                    # dcc.Loading v2 overlay API: keeps the underlying
                    # Graph mounted (so uirevision survives) and layers
                    # a translucent spinner overlay only when these
                    # specific outputs are being computed. The 200 ms
                    # `delay_show` suppresses flicker on pure styling
                    # re-renders that complete in < 200 ms.
                    dcc.Loading(
                        id="plot-loading",
                        type="circle",
                        # Brand bright-blue: reads on both light and
                        # dark backdrops. Navy disappeared against the
                        # dark body.
                        color="#0083FF",
                        delay_show=200,
                        parent_style={"position": "relative"},
                        overlay_style={
                            "visibility": "visible",
                            # Theme-aware tint defined in _GLOBAL_CSS:
                            # opaque enough (92 %) to obscure the
                            # empty-state placeholder text.
                            "backgroundColor": "var(--plot-loading-bg)",
                            "transition": "visibility 0.1s",
                        },
                        # Dash's TargetComponents TypedDict is declared
                        # with zero keys, so any literal trips mypy.
                        # The runtime accepts any {component_id: prop |
                        # list[prop]} mapping.
                        target_components={  # type: ignore[arg-type]
                            "main-plot": "figure",
                            "tabs-plot-container": "children",
                        },
                        children=[
                            html.Div(
                                dcc.Graph(
                                    id="main-plot",
                                    # Blank placeholder — the onboarding
                                    # hint above the plot panel handles
                                    # the empty-state messaging.
                                    figure=_empty_figure(),
                                    config=_PLOTLY_CONFIG,  # type: ignore[arg-type]
                                    className="ui-plot-graph",
                                ),
                                id="single-plot-container",
                            ),
                            # Cycle Life "Capacity table" sub-view
                            # writes its layout into this container.
                            # The three figure sub-views go through
                            # main-plot.figure instead.
                            html.Div(
                                id="tabs-plot-container",
                                style={"display": "none"},
                            ),
                        ],
                    ),
                    # Horizontal drag handle for plot height (see CSS
                    # .ui-plot-h-divider + clientside JS in app.py).
                    # Lives OUTSIDE the Loading wrapper so the spinner
                    # only covers the plot, not the drag handle.
                    html.Div(
                        id="plot-h-divider",
                        className="ui-plot-h-divider",
                        title="Drag to resize the plot height",
                    ),
                ],
                id="plot-resizer",
                className="ui-plot-resizer",
            ),
            # Reserve a fixed height for the warnings band so empty → populated
            # → empty transitions don't reflow the page below.
            html.Div(
                id="plot-warnings",
                style={"minHeight": "40px"},
            ),
        ]
    )


def register_callbacks(app: dash.Dash) -> None:
    # --- Onboarding hint: show when connected + staged is empty ---------
    @app.callback(
        Output("onboarding-hint", "style"),
        Input("staging-version", "data"),
        Input("connection-version", "data"),
    )
    def _toggle_onboarding_hint(_s, _c):  # type: ignore[no-untyped-def]
        state = get_state()
        if state.get("client") is None:
            return {"display": "none"}
        if state.get("staged_items"):
            return {"display": "none"}
        return {}

    # --- Main render callback --------------------------------------------
    # Outputs ONLY to figure/children/style props — never recreates
    # dcc.Graph, so uirevision keeps zoom on styling changes.
    @app.callback(
        Output("main-plot", "figure"),
        Output("tabs-plot-container", "children"),
        Output("single-plot-container", "style"),
        Output("tabs-plot-container", "style"),
        Output("plot-warnings", "children"),
        Output("plot-cache-caption", "children"),
        Output("plot-version", "data", allow_duplicate=True),
        Output("picker-grid", "selectedRows", allow_duplicate=True),
        Input("opt-refresh-btn", "n_clicks"),
        Input("picker-payload", "data"),
        Input("plot-options", "data"),
        Input("theme", "data"),
        State("opt-auto", "value"),
        State("plot-version", "data"),
        State("picker-grid", "selectedRows"),
        State("opt-summary-view", "data"),
        prevent_initial_call=True,
    )
    def _render_plot(  # type: ignore[no-untyped-def]
        refresh_clicks, payload, options, theme_store, auto, plot_version,
        grid_selected, sum_view,
    ):
        triggered = ctx.triggered_id
        is_refresh = (triggered == "opt-refresh-btn")
        # Read theme from the dedicated `theme` Store rather than the
        # echoed-into-plot-options copy. _aggregate echoes the theme
        # into plot-options, but the two callbacks (_aggregate and
        # _render_plot) fire in parallel when the user clicks the
        # toggle — so plot-options may still hold the OLD theme when
        # this callback runs. The theme Store itself is the single
        # source of truth.
        theme = str(theme_store or (options or {}).get("theme") or "light")
        # Theme toggle is a render trigger even when Auto-refresh is off:
        # the user just clicked it and expects an immediate flip. We
        # detect it by comparing against the last-rendered theme rather
        # than ctx.triggered_id — when the user clicks ☾, both the
        # `theme` Store AND `plot-options` (via _aggregate echoing the
        # theme key) update, and the trigger that lands here can be
        # either one, depending on Store-settle order. The value-compare
        # is unambiguous. We do NOT set force_refresh — no data
        # re-download, just a re-render with the new Plotly template.
        state = get_state()
        last_theme = (state.get("last_plot") or {}).get("theme")
        theme_changed = last_theme is not None and last_theme != theme
        if not is_refresh and not auto and not theme_changed:
            return (no_update, no_update, no_update, no_update,
                    no_update, no_update, no_update, no_update)
        if not payload:
            return (
                _empty_figure(theme=theme),
                no_update,
                {},                      # single-plot visible
                {"display": "none"},     # tabs hidden
                "",
                "",
                no_update,
                no_update,
            )

        client = state.get("client")
        if client is None:
            return (
                _empty_figure("Not connected.", theme=theme),
                no_update,
                {},
                {"display": "none"},
                "",
                "",
                no_update,
                no_update,
            )

        options = options or {}
        style = _style_from_dict(options.get("style"))
        mode = options.get("mode", "xy")
        cycle = options.get("cycle")
        title = options.get("title", "")
        x_axis = options.get("x_axis", "time")
        y_axis = options.get("y_axis", "voltage")
        y2_axis = options.get("y2_axis", "none")
        color_by_status = bool(options.get("color_by_status", False))
        width_scale = float(options.get("width_scale", 1.0))
        specific_capacity = bool(options.get("specific_capacity", False))

        raw_data = state.setdefault("raw_data", {})
        cathode_masses = state.setdefault("cathode_masses", {})

        # height=None lets Plotly inherit container height (driven by the
        # .ui-plot-graph CSS rule); width_frac stays 1.0 (plot fills the
        # right column — no padding).
        result = build_figure_for_payload(
            client, payload, mode, cycle, title,
            1.0, None,
            x_axis=x_axis, y_axis=y_axis, y2_axis=y2_axis,
            color_by_status=color_by_status, width_scale=width_scale, style=style,
            force_refresh=is_refresh, specific_capacity=specific_capacity,
            raw_data=raw_data, cathode_masses=cathode_masses,
            theme=theme,
        )

        # Per-item errors: stash them and deselect those rows in the grid.
        new_selected_rows: Any = no_update
        if result.errors:
            broken = state.setdefault("broken_items", {})
            broken.update(result.errors)
            err_ids = set(result.errors.keys())
            new_selected_rows = [
                r for r in (grid_selected or []) if r.get("item_id") not in err_ids
            ]

        warnings: list[Any] = []
        if result.skipped_labels:
            warnings.append(dbc.Alert(
                "No cycling files for: " + ", ".join(result.skipped_labels)
                + " — omitted.",
                color="warning",
            ))
        if result.skipped_no_mass:
            warnings.append(dbc.Alert(
                "Skipped from specific capacity plot — no cathode mass recorded: "
                + ", ".join(result.skipped_no_mass),
                color="warning",
            ))
        if result.error_message:
            warnings.append(dbc.Alert(
                f"Plot failed: {result.error_message}",
                color="danger",
            ))

        if result.fig is None:
            return (
                _empty_figure(
                    "All staged cells are missing cycling data — see "
                    "warnings above."
                    if warnings else "",
                    theme=theme,
                ),
                no_update,
                {},
                {"display": "none"},
                warnings, "",
                no_update,
                new_selected_rows,
            )

        # Persist for re-display + export
        state["last_fig"] = result.fig
        state["last_plot"] = result.last_plot
        if result.cycle_summaries is not None:
            state["last_cycle_summaries"] = result.cycle_summaries
        else:
            state.pop("last_cycle_summaries", None)

        revision = _ui_revision(mode, x_axis, y_axis, y2_axis, cycle)

        cache_caption = ""
        if (result.hits + result.misses) > 0:
            n = result.hits + result.misses
            cache_caption = (
                f"Files: {result.hits}/{n} cache hit · "
                f"{result.misses}/{n} re-downloaded."
            )

        if isinstance(result.fig, list):
            # Cycle Life — dispatch by active sub-view. Three figure
            # sub-views write to main-plot.figure; "Capacity table"
            # writes the table layout into tabs-plot-container.
            fig_by_title = {t: f for t, f in result.fig}
            active = sum_view or "Discharge capacity"
            if active == "Capacity table" and result.cycle_summaries:
                return (
                    no_update,
                    _capacity_table_layout(
                        result.cycle_summaries, result.payload,
                    ),
                    {"display": "none"},        # hide main plot
                    {},                          # show table container
                    warnings, cache_caption,
                    (plot_version or 0) + 1,
                    new_selected_rows,
                )
            active_fig = fig_by_title.get(
                active, next(iter(fig_by_title.values())),
            )
            return (
                _wrap_uirev(active_fig, f"{revision}|{active}"),
                no_update,
                {},                              # show main plot
                {"display": "none"},             # hide table container
                warnings, cache_caption,
                (plot_version or 0) + 1,
                new_selected_rows,
            )

        # Single-fig modes: just update the figure prop. The Graph stays
        # mounted, so Plotly's uirevision keeps zoom/pan.
        return (
            _wrap_uirev(result.fig, revision),
            no_update,                      # tabs container untouched
            {},                             # show main plot
            {"display": "none"},            # hide tabs
            warnings, cache_caption,
            (plot_version or 0) + 1,
            new_selected_rows,
        )

    # --- Cycle Life sub-view click handler -------------------------------
    # Fires when the user clicks one of the sub-view buttons (Discharge /
    # Charge / CE % / Table). Reads the cached figures + summaries from
    # session state (set by _render_plot's last successful summary
    # run) and writes the active sub-view to main-plot / tabs container.
    # No-op when there's no cached cycle-life result.
    @app.callback(
        Output("main-plot", "figure", allow_duplicate=True),
        Output("tabs-plot-container", "children", allow_duplicate=True),
        Output("single-plot-container", "style", allow_duplicate=True),
        Output("tabs-plot-container", "style", allow_duplicate=True),
        Input("opt-summary-view", "data"),
        State("plot-options", "data"),
        prevent_initial_call=True,
    )
    def _render_summary_subview(active, options):  # type: ignore[no-untyped-def]
        state = get_state()
        last_fig = state.get("last_fig")
        if not isinstance(last_fig, list):
            return no_update, no_update, no_update, no_update
        summaries = state.get("last_cycle_summaries")
        last_payload = (state.get("last_plot") or {}).get("payload") or {}
        options = options or {}
        mode = options.get("mode", "xy")
        cycle = options.get("cycle")
        x_axis = options.get("x_axis", "time")
        y_axis = options.get("y_axis", "voltage")
        y2_axis = options.get("y2_axis", "none")
        revision = _ui_revision(mode, x_axis, y_axis, y2_axis, cycle)
        if active == "Capacity table" and summaries:
            return (
                no_update,
                _capacity_table_layout(summaries, last_payload),
                {"display": "none"},
                {},
            )
        fig_by_title = {t: f for t, f in last_fig}
        active_fig = fig_by_title.get(
            active or "Discharge capacity",
            next(iter(fig_by_title.values())),
        )
        return (
            _wrap_uirev(active_fig, f"{revision}|{active}"),
            no_update,
            {},
            {"display": "none"},
        )

    # --- Capacity table cycle selector (only mounted in summary mode) -----
    @app.callback(
        Output("capacity-table", "children"),
        Input("capacity-table-cycle", "value"),
        prevent_initial_call=True,
    )
    def _update_capacity_table(cycle):  # type: ignore[no-untyped-def]
        if cycle is None:
            return no_update
        state = get_state()
        summaries = state.get("last_cycle_summaries")
        if not summaries:
            return no_update
        payload = state.get("last_plot", {}).get("payload", {})
        item_ids = {label: spec.get("item_id", "") for label, spec in payload.items()}
        table = _build_capacity_table(summaries, int(cycle), item_ids)
        return dbc.Table.from_dataframe(
            table, striped=True, bordered=True, hover=True, size="sm",
        ).children
