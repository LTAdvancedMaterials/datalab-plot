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

**Vertical stability**: no ``dcc.Loading`` wrapper (it momentarily
collapses the section height); a fixed-height warnings container so
appearing/disappearing alerts don't reflow the page; horizontal padding
on the plot wrapper instead of variable left/right padding that would
shift the plot box between renders.
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


def _empty_figure(message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message, xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(size=16, color="#888"),
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(
        template="plotly_white", margin=dict(l=20, r=20, t=20, b=20), height=320,
    )
    return fig


def _wrap_uirev(fig: go.Figure, revision: str) -> go.Figure:
    fig.update_layout(uirevision=revision)
    return fig


def _tabs_layout(
    tab_figs: list[tuple[str, go.Figure]],
    cycle_summaries: dict[str, pd.DataFrame] | None,
    payload: dict[str, dict[str, Any]],
) -> dbc.Tabs:
    """Build the Cycle Life tabs (3 figures + capacity table)."""
    tabs: list[dbc.Tab] = []
    for title, fig in tab_figs:
        slug = title.lower().replace(" ", "-")
        tabs.append(
            dbc.Tab(
                dcc.Graph(
                    id=f"main-plot-{slug}",
                    figure=fig,
                    config=_PLOTLY_CONFIG,  # type: ignore[arg-type]
                    style={"width": "100%"},
                ),
                label=title, tab_id=slug,
            )
        )
    if cycle_summaries:
        tabs.append(
            dbc.Tab(
                _capacity_table_layout(cycle_summaries, payload),
                label="Capacity table",
                tab_id="capacity-table",
            )
        )
    return dbc.Tabs(tabs, id="main-tabs", active_tab=tabs[0].tab_id)


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
            # Single-fig modes: the main Graph is mounted ONCE here and
            # never replaced — only its `figure` prop is written by the
            # plot callback. This is what makes uirevision actually preserve
            # zoom across styling changes.
            html.Div(
                dcc.Graph(
                    id="main-plot",
                    figure=_empty_figure(
                        "Tick rows in the picker, then click Refresh "
                        "(or enable Auto-refresh)."
                    ),
                    config=_PLOTLY_CONFIG,  # type: ignore[arg-type]
                    style={"width": "100%"},
                ),
                id="single-plot-container",
            ),
            # Cycle Life mode: the tabs are written into this container.
            # Mode-switch callback toggles visibility between the two.
            html.Div(id="tabs-plot-container", style={"display": "none"}),
            # Reserve a fixed height for the warnings band so empty → populated
            # → empty transitions don't reflow the page below.
            html.Div(
                id="plot-warnings",
                style={"minHeight": "40px"},
            ),
            html.Div(id="plot-cache-caption", className="ui-meta"),
        ]
    )


def register_callbacks(app: dash.Dash) -> None:
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
        State("opt-auto", "value"),
        State("plot-version", "data"),
        State("picker-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def _render_plot(  # type: ignore[no-untyped-def]
        refresh_clicks, payload, options, auto, plot_version,
        grid_selected,
    ):
        triggered = ctx.triggered_id
        is_refresh = (triggered == "opt-refresh-btn")
        # Gate: only run when Refresh was clicked OR Auto is on.
        if not is_refresh and not auto:
            return (no_update, no_update, no_update, no_update,
                    no_update, no_update, no_update, no_update)
        if not payload:
            return (
                _empty_figure(
                    "Tick rows in the picker, then click Refresh "
                    "(or enable Auto-refresh)."
                ),
                no_update,
                {},                      # single-plot visible
                {"display": "none"},     # tabs hidden
                "",
                "",
                no_update,
                no_update,
            )

        state = get_state()
        client = state.get("client")
        if client is None:
            return (
                _empty_figure("Not connected."),
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
        width_frac = float(options.get("width_frac", 0.9))
        height_px = int(options.get("height_px", 520))
        specific_capacity = bool(options.get("specific_capacity", False))

        raw_data = state.setdefault("raw_data", {})
        cathode_masses = state.setdefault("cathode_masses", {})

        result = build_figure_for_payload(
            client, payload, mode, cycle, title,
            width_frac, height_px,
            x_axis=x_axis, y_axis=y_axis, y2_axis=y2_axis,
            color_by_status=color_by_status, width_scale=width_scale, style=style,
            force_refresh=is_refresh, specific_capacity=specific_capacity,
            raw_data=raw_data, cathode_masses=cathode_masses,
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
                    "Nothing to plot — see warnings above." if warnings
                    else "Tick rows in the picker."
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
            # Cycle Life — tabs replace the main plot.
            tab_figs = [(t, _wrap_uirev(f, f"{revision}|{t}")) for t, f in result.fig]
            tabs = _tabs_layout(tab_figs, result.cycle_summaries, result.payload)
            return (
                no_update,                  # don't touch main-plot.figure
                tabs,                       # populate tabs container
                {"display": "none"},        # hide main plot
                {},                         # show tabs
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
