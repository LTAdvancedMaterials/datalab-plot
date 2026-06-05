"""Plotly figure builders, layout styling, data acquisition and plot render.

The figure builders consume :mod:`datalab_plot.series` for all data
preparation — the per-cycle iteration / dQ-dV / summary logic lives there and
is shared with the matplotlib renderers in :mod:`datalab_plot.plots.echem`.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from datalab_plot.client import DatalabPlotClient
from datalab_plot.gui.constants import PlotStyle
from datalab_plot.gui.helpers import (
    _axis_col_in,
    _axis_label,
    _axis_resets,
    _axis_series,
    _desaturate_css,
    _mpl_colorscale,
    _rgba_to_css,
    _status_color,
)
from datalab_plot.gui.picker_panel import _current_picker_df, _set_initial
from datalab_plot.parsers.echem import (
    detect_status_column,
    is_cycling_file,
    load_echem,
    split_by_status,
    split_half_cycles,
)
from datalab_plot.plots.echem import _assign_colors, _normalise_items
from datalab_plot.series import (
    cycle_cmap,
    dqdv_series,
    summary_series,
    voltage_capacity_series,
    voltage_time_series,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trace-style helper
# ---------------------------------------------------------------------------

def _trace_style(
    style: PlotStyle | None,
    color: str,
    width: float,
    *,
    dash: str | None = None,
    secondary: bool = False,
) -> dict:
    """Return ``mode`` + per-trace appearance kwargs honouring marker_mode.

    * ``"lines"`` (default) — ``dash`` is applied if given.
    * ``"lines+markers"`` — line + small open markers; secondary uses ``diamond``.
    * ``"markers"`` — dots only; secondary uses ``"x"`` symbol.
    """
    mode = style.marker_mode if style else "lines"
    size = style.marker_size if style else 6.0

    line_cfg: dict = dict(color=color, width=width)
    if dash:
        line_cfg["dash"] = dash

    if mode == "markers":
        symbol = "x" if secondary else "circle"
        return dict(
            mode="markers",
            marker=dict(color=color, size=size, symbol=symbol, opacity=0.8),
        )
    if mode == "lines+markers":
        symbol = "diamond" if secondary else "circle"
        return dict(
            mode="lines+markers",
            line=line_cfg,
            marker=dict(color=color, size=size, symbol=symbol, opacity=0.75),
        )
    return dict(mode="lines", line=line_cfg)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _layout(
    fig: go.Figure, height: int, title: str | None = None,
    style: PlotStyle | None = None, *, secondary_y: bool = False,
) -> go.Figure:
    style = style or PlotStyle()
    legend_cfg = {
        # yanchor="top": the legend's TOP edge is pinned just below the
        # x-axis title, so when it wraps to a second row the extra rows
        # grow *downward* (into the auto-expanding bottom margin) rather
        # than upward over the axis title.
        "below": dict(
            orientation="h", yanchor="top", y=-0.22, xanchor="center", x=0.5,
        ),
        # Inset into the plot body (corner at 0.97/0.97 paper coords) so the
        # legend box sits clear of the mirrored border rather than straddling
        # it.
        "overlaid": dict(
            orientation="v", yanchor="top", y=0.97, xanchor="right", x=0.97,
            bgcolor="rgba(255,255,255,0.75)",
            bordercolor="rgba(0,0,0,0.2)", borderwidth=1,
        ),
    }.get(style.legend_mode)
    if legend_cfg is not None:
        # Legend text nominally inherits layout.font, but set it explicitly
        # so the Text-size control resizes legend entries too.
        legend_cfg["font"] = dict(size=style.font_size)
    if legend_cfg is not None and secondary_y and style.legend_mode == "overlaid":
        # A dual-Y figure shrinks the x-axis domain (e.g. to [0, 0.94]) to
        # make room for the right-hand axis. Legend x is in paper coords
        # where 1.0 is the figure edge, so the default 0.97 lands in the
        # gap *outside* the axes panel. Re-anchor to the domain's right edge.
        dom = fig.layout.xaxis.domain
        right = dom[1] if dom is not None else 1.0
        legend_cfg["x"] = right - 0.03
    fig.update_layout(
        title=title or None,
        template="plotly_white",
        margin=dict(
            l=60, r=20, t=50 if title else 30,
            b=80 if style.legend_mode == "below" else 40,
        ),
        showlegend=(style.legend_mode != "none"),
        legend=legend_cfg,
        hovermode="closest",
        height=height,
        # layout.font is the global default; tick labels inherit it. Axis
        # titles and the plot title carry their own font objects, so set
        # those explicitly too — otherwise the Text-size control only
        # resizes tick labels and the legend.
        font=dict(size=style.font_size),
        title_font=dict(size=style.font_size + 3),
    )
    fig.update_xaxes(
        showgrid=style.grid_x,
        showline=style.border, mirror=style.border,
        linecolor="#444444", linewidth=1,
        tickfont=dict(size=style.font_size),
        title_font=dict(size=style.font_size),
    )
    fig.update_yaxes(
        showgrid=style.grid_y,
        showline=style.border, mirror=style.border,
        linecolor="#444444", linewidth=1,
        tickfont=dict(size=style.font_size),
        title_font=dict(size=style.font_size),
    )
    # Manual axis limits — applied only when both bounds of an axis are
    # given. The primary y-range may leak onto a secondary axis in dual-Y
    # figures; _plotly_xy re-scopes it afterwards.
    if style.x_min is not None and style.x_max is not None:
        fig.update_xaxes(range=[style.x_min, style.x_max])
    if style.y_min is not None and style.y_max is not None:
        fig.update_yaxes(range=[style.y_min, style.y_max])
    return fig


# ---------------------------------------------------------------------------
# Figure builders. Each takes the same items+raw shape so a single dispatch
# (_build_plotly) can serve both Plot-click and live-update reruns.
# ---------------------------------------------------------------------------

def _plotly_summary(items, raw, colors, height, width_scale: float = 1.0,
                    style: PlotStyle | None = None,
                    masses: dict[str, float] | None = None) -> go.Figure:
    specific = bool(masses)
    y_axis_label = "Specific discharge capacity (mAh/g)" if specific else "Discharge capacity (mAh)"
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Discharge capacity", "Coulombic efficiency"),
        # Wide gap so the right panel's "Coulombic efficiency (%)" axis title
        # isn't crowded by the left panel.
        horizontal_spacing=0.2,
    )
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        mass_g = masses.get(label) if masses else None
        s = summary_series(raw[label], mass_g=mass_g)
        if specific and s.discharge_mah_g is None:
            continue
        y_data = s.discharge_mah_g if specific else s.discharge_mah
        y_unit = "mAh/g" if specific else "mAh"
        color = _rgba_to_css(colors[label])
        w = 1.6 * width_scale
        fig.add_trace(
            go.Scatter(
                x=s.cycle, y=y_data,
                **_trace_style(style, color, w),
                name=label,
                legendgroup=label,
                hovertemplate=(
                    f"cycle %{{x}}<br>%{{y:.2f}} {y_unit}"
                    "<extra>%{fullData.name}</extra>"
                ),
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=s.cycle, y=s.ce_percent,
                **_trace_style(style, color, w),
                name=label,
                legendgroup=label, showlegend=False,
                hovertemplate="cycle %{x}<br>%{y:.2f}%<extra>%{fullData.name}</extra>",
            ),
            row=1, col=2,
        )
    fig.update_xaxes(title_text="Cycle number", row=1, col=1)
    fig.update_xaxes(title_text="Cycle number", row=1, col=2)
    fig.update_yaxes(title_text=y_axis_label, row=1, col=1)
    fig.update_yaxes(title_text="Coulombic efficiency (%)", row=1, col=2, range=[90, 102])
    fig = _layout(fig, height, title="Cycle life", style=style)
    # make_subplots puts the subplot titles flush with the panel top, where
    # they collide with the mirrored border and the overall title. Drop the
    # panels and stack vertically: overall title (margin) · subplot titles ·
    # panels.
    fig.update_yaxes(domain=[0.0, 0.88])
    for ann in fig.layout.annotations[:2]:
        ann.update(y=0.93, yanchor="bottom")
    fig.update_layout(margin_t=80)
    return fig


def _plotly_voltage_capacity(items, raw, colors, height, width_scale: float = 1.0,
                             style: PlotStyle | None = None) -> go.Figure:
    """V-Q for every cycle of every cell. Each cell gets a distinct
    single-hue colour-to-dark gradient (orange first by default). Late
    cycle = dark / saturated end. The ``colors`` param is unused here.

    When ``style.colorbar`` is set, a per-cell cycle-number colorbar is added
    on the right.
    """
    show_cbar = bool(style and style.colorbar)
    fig = go.Figure()
    n_colorbars = 0
    for cell_idx, it in enumerate(items):
        label = it["label"]
        if label not in raw:
            continue
        traces = voltage_capacity_series(raw[label])
        if not traces:
            continue
        cmap, cmap_name = cycle_cmap(cell_idx)

        # One invisible legend-only trace per cell so the legend stays compact
        # (one entry per cell, coloured with the colormap's mid-point) and the
        # many real per-cycle traces below share its legendgroup for toggling.
        legend_color = _rgba_to_css(cmap(0.5))
        legend_w = 3 * width_scale
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None],
                **_trace_style(style, legend_color, legend_w),
                name=f"{label}  ({cmap_name})",
                legendgroup=label, showlegend=True,
            )
        )

        for t in traces:
            color = _rgba_to_css(cmap(t.frac))
            fig.add_trace(
                go.Scattergl(
                    x=t.x, y=t.y,
                    **_trace_style(style, color, 1.0 * width_scale),
                    legendgroup=label, showlegend=False,
                    hovertemplate=(
                        f"<b>{label}</b> · cycle {t.cycle_id}<br>"
                        "%{x:.2f} mAh<br>%{y:.3f} V<extra></extra>"
                    ),
                )
            )

        if show_cbar:
            # Invisible marker trace carrying the cell's colormap as a
            # plotly colorscale → renders a cycle-number colorbar. One per
            # cell, staggered along the right edge.
            fig.add_trace(
                go.Scatter(
                    x=[None], y=[None], mode="markers",
                    marker=dict(
                        colorscale=_mpl_colorscale(cmap),
                        cmin=traces[0].cycle_id, cmax=traces[-1].cycle_id,
                        color=[traces[0].cycle_id],
                        showscale=True,
                        colorbar=dict(
                            title=dict(text=f"{label}<br>cycle", side="right"),
                            len=0.92, thickness=14,
                            x=1.02 + 0.16 * n_colorbars,
                        ),
                    ),
                    hoverinfo="skip", showlegend=False,
                )
            )
            n_colorbars += 1

    fig.update_xaxes(title_text="Capacity (mAh)")
    fig.update_yaxes(title_text="Voltage (V)")
    fig = _layout(fig, height, title="Voltage vs capacity, all cycles", style=style)
    if n_colorbars:
        # Widen the right margin so the colorbars don't overlap the plot.
        fig.update_layout(margin=dict(r=20 + 90 * n_colorbars))
    return fig


def _plotly_dqdv(items, raw, colors, cycle, height, width_scale: float = 1.0,
                 style: PlotStyle | None = None) -> go.Figure:
    fig = go.Figure()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        color = _rgba_to_css(colors[label])
        for t in dqdv_series(raw[label], cycle):
            fig.add_trace(
                go.Scatter(
                    x=t.x, y=t.y,
                    **_trace_style(style, color, 1.4 * width_scale),
                    name=label, connectgaps=False,
                    hovertemplate="%{x:.3f} V<br>%{y:.2f} mA/V<extra>%{fullData.name}</extra>",
                )
            )
    fig.update_xaxes(title_text="Voltage (V)")
    fig.update_yaxes(title_text="dQ/dV (mA/V)")
    return _layout(fig, height, title=f"dQ/dV, cycle {cycle}", style=style)


def _plotly_voltage_time(items, raw, colors, height, width_scale: float = 1.0,
                         style: PlotStyle | None = None) -> go.Figure:
    fig = go.Figure()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        color = _rgba_to_css(colors[label])
        s = voltage_time_series(raw[label])
        fig.add_trace(
            go.Scatter(
                x=s.x, y=s.y,
                **_trace_style(style, color, 1.0 * width_scale),
                name=label,
                hovertemplate="%{x:.2f} h<br>%{y:.3f} V<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_xaxes(title_text="Time (h)")
    fig.update_yaxes(title_text="Voltage (V)")
    return _layout(fig, height, title="Voltage vs time", style=style)


# --- Generic XY plot --------------------------------------------------------
# _plotly_xy is split into three trace-emitting helpers (primary status,
# primary per-cell, secondary axis) plus a small title builder. Each helper
# receives the `add` callable that knows whether the figure has a secondary
# axis, so trace placement stays in one place.

def _xy_primary_status(
    add, items, raw, x_axis, y_axis, needs_gaps, base_w,
    style: PlotStyle | None = None,
) -> None:
    """Primary Y traces, split into per-step segments coloured by status."""
    seen_status: set[str] = set()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        df = raw[label]
        tmp, x_col = _axis_col_in(df, x_axis)
        tmp, axis_col = _axis_col_in(tmp, y_axis)
        status_col = detect_status_column(df)
        if status_col is None:
            xs, ys = (
                split_half_cycles(tmp, x_col, axis_col)
                if needs_gaps else
                (tmp[x_col].to_numpy(), tmp[axis_col].to_numpy())
            )
            add({
                "x": xs, "y": ys,
                **_trace_style(style, "#777", base_w),
                "name": f"{label} (no status)",
                "connectgaps": False,
                "hovertemplate": (
                    f"<b>{label}</b><br>"
                    "%{x:.3f}<br>%{y:.3f}<extra></extra>"
                ),
            }, secondary=False)
            continue
        for xs, ys, sval in split_by_status(tmp, x_col, axis_col, status_col):
            show = sval not in seen_status
            seen_status.add(sval)
            add({
                "x": xs, "y": ys,
                **_trace_style(style, _status_color(sval), base_w),
                "name": sval,
                "legendgroup": sval,
                "showlegend": show,
                "hovertemplate": (
                    f"<b>{label}</b> · {sval}<br>"
                    "%{x:.3f}<br>%{y:.3f}<extra></extra>"
                ),
            }, secondary=False)


def _xy_primary_cells(
    add, items, raw, colors, x_axis, y_axis, has_y2, needs_gaps, base_w, ylabel,
    style: PlotStyle | None = None,
) -> None:
    """Primary Y traces, one per cell, coloured per cell."""
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        df = raw[label]
        if needs_gaps:
            tmp, x_col = _axis_col_in(df, x_axis)
            tmp, axis_col = _axis_col_in(tmp, y_axis)
            xs, ys = split_half_cycles(tmp, x_col, axis_col)
        else:
            xs = _axis_series(df, x_axis)
            ys = _axis_series(df, y_axis)
        cell_color = _rgba_to_css(colors[label])
        add({
            "x": xs, "y": ys,
            **_trace_style(style, cell_color, base_w),
            "name": (f"{label} (left)" if has_y2 else label),
            "connectgaps": False,
            "legendgroup": label,
            "showlegend": True,
            "hovertemplate": (
                f"<b>{label}</b><br>"
                "%{x:.3f}<br>"
                f"%{{y:.3f}} {ylabel.split('(')[-1].rstrip(')')}"
                "<extra></extra>"
            ),
        }, secondary=False)


def _xy_secondary(
    add, items, raw, colors, x_axis, y2_axis, color_by_status, needs_gaps, base_w, y2label,
    style: PlotStyle | None = None,
) -> None:
    """Secondary (right) Y traces — cell-coloured; dashed lines or x-markers."""
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        df = raw[label]
        if needs_gaps:
            tmp, x_col = _axis_col_in(df, x_axis)
            tmp, axis_col = _axis_col_in(tmp, y2_axis)
            xs, ys = split_half_cycles(tmp, x_col, axis_col)
        else:
            xs = _axis_series(df, x_axis)
            ys = _axis_series(df, y2_axis)
        cell_color = _desaturate_css(colors[label], amount=0.5)
        add({
            "x": xs, "y": ys,
            **_trace_style(style, cell_color, base_w, dash="dash", secondary=True),
            "name": f"{label} ({y2_axis})",
            "connectgaps": False,
            # When colouring by status, the left legend is by step name;
            # the right legend keeps per-cell entries (one per cell) so
            # users can identify which dashed line belongs to which cell.
            "legendgroup": (f"y2:{label}" if color_by_status else label),
            "showlegend": True if color_by_status else False,
            "hovertemplate": (
                f"<b>{label}</b><br>"
                "%{x:.3f}<br>"
                f"%{{y:.3f}} {y2label.split('(')[-1].rstrip(')')}"
                "<extra></extra>"
            ),
        }, secondary=True)


def _plotly_xy(
    items, raw, colors,
    x_axis: str, y_axis: str, y2_axis: str, height: int,
    color_by_status: bool = False,
    width_scale: float = 1.0,
    style: PlotStyle | None = None,
) -> go.Figure:
    """Generic X-Y plot with axes chosen from AXIS_OPTIONS.

    * If ``color_by_status`` is True, the Y axis is split into per-step
      segments coloured by status (``CC_Chg``, ``CV_Chg``, ``Rest``, …).
    * If ``y2_axis != "none"`` the figure adds a secondary right-hand axis;
      its trace is always cell-coloured + dashed (regardless of
      ``color_by_status``), so the right axis remains readable when the
      left is fragmented by status.
    * The two options compose: status-coloured Y on the left, cell-coloured
      dashed Y2 on the right.

    Half-cycle NaN gaps are applied to the Y2 / non-status Y traces when
    any axis is a half-cycle-resetting column (Capacity). Status-coloured
    traces inherently break at each transition, so they don't need the gap.

    ``width_scale`` multiplies every line width.
    """
    # "if available": if status colouring was requested but none of the
    # selected cells actually carry a step column, fall back to per-cell
    # colouring rather than drawing everything as undifferentiated grey.
    if color_by_status and not any(
        it["label"] in raw and detect_status_column(raw[it["label"]]) is not None
        for it in items
    ):
        color_by_status = False

    has_y2 = bool(y2_axis) and y2_axis != "none"
    needs_gaps = (
        _axis_resets(x_axis)
        or _axis_resets(y_axis)
        or (has_y2 and _axis_resets(y2_axis))
    )
    xlabel = _axis_label(x_axis)
    ylabel = _axis_label(y_axis)
    y2label = _axis_label(y2_axis) if has_y2 else ""

    fig = (
        make_subplots(specs=[[{"secondary_y": True}]])
        if has_y2 else go.Figure()
    )
    # Use go.Scatter (not Scattergl) when secondary_y is in play; the GL
    # path occasionally mis-renders against secondary axes in plotly.
    cls = go.Scatter if has_y2 else go.Scattergl
    base_w = 1.0 * width_scale

    def _add(trace_kwargs, secondary=False):
        if has_y2:
            fig.add_trace(cls(**trace_kwargs), secondary_y=secondary)
        else:
            fig.add_trace(cls(**trace_kwargs))

    # --- Primary Y axis ----------------------------------------------------
    if color_by_status:
        _xy_primary_status(_add, items, raw, x_axis, y_axis, needs_gaps, base_w, style)
    else:
        _xy_primary_cells(
            _add, items, raw, colors, x_axis, y_axis, has_y2, needs_gaps, base_w, ylabel, style
        )

    # --- Secondary Y axis (always cell-coloured, dashed) ------------------
    if has_y2:
        _xy_secondary(
            _add, items, raw, colors, x_axis, y2_axis,
            color_by_status, needs_gaps, base_w, y2label, style,
        )

    # --- Axis labels + title ----------------------------------------------
    fig.update_xaxes(title_text=xlabel)
    suffix = " (by status)" if color_by_status else ""
    xtitle = xlabel.split(" (")[0]
    ytitle = ylabel.split(" (")[0]
    if has_y2:
        fig.update_yaxes(title_text=ylabel, secondary_y=False)
        fig.update_yaxes(title_text=y2label, secondary_y=True)
        y2title = y2label.split(" (")[0]
        title_text = f"{ytitle} & {y2title} vs {xtitle}{suffix}"
    else:
        fig.update_yaxes(title_text=ylabel)
        title_text = f"{ytitle} vs {xtitle}{suffix}"
    fig = _layout(fig, height, title=title_text, style=style, secondary_y=has_y2)
    if has_y2:
        # The secondary y-axis would otherwise draw its own horizontal
        # gridlines, offset from the primary axis's — a confusing double
        # set. Keep only the primary axis's grid.
        fig.update_yaxes(showgrid=False, secondary_y=True)
        # Re-scope the secondary y-axis range: _layout's global y-range
        # (style.y_min/y_max) leaks onto it, so apply the y2 limits — or
        # restore autorange — explicitly.
        if style is not None:
            if style.y2_min is not None and style.y2_max is not None:
                fig.update_yaxes(range=[style.y2_min, style.y2_max], secondary_y=True)
            elif style.y_min is not None and style.y_max is not None:
                fig.update_yaxes(autorange=True, secondary_y=True)
    return fig


def _build_plotly(
    payload: dict[str, dict[str, Any]],
    raw: dict[str, pd.DataFrame],
    mode: str,
    cycle: int | None,
    title: str | None,
    height: int,
    x_axis: str = "time",
    y_axis: str = "voltage",
    y2_axis: str = "none",
    color_by_status: bool = False,
    width_scale: float = 1.0,
    style: PlotStyle | None = None,
    masses: dict[str, float] | None = None,
) -> go.Figure:
    items = _normalise_items(payload)
    colors = _assign_colors(items)
    if mode == "summary":
        fig = _plotly_summary(items, raw, colors, height,
                              width_scale=width_scale, style=style, masses=masses)
    elif mode == "voltage_capacity":
        fig = _plotly_voltage_capacity(items, raw, colors, height,
                                       width_scale=width_scale, style=style)
    elif mode == "dqdv":
        fig = _plotly_dqdv(items, raw, colors, int(cycle or 1), height,
                           width_scale=width_scale, style=style)
    elif mode == "voltage_time":
        # Kept for backwards compatibility (cached figures, library parity).
        # In the GUI, V vs t is reached via mode="xy" with x=time, y=voltage.
        fig = _plotly_voltage_time(items, raw, colors, height,
                                   width_scale=width_scale, style=style)
    elif mode == "xy":
        fig = _plotly_xy(
            items, raw, colors, x_axis, y_axis, y2_axis, height,
            color_by_status=color_by_status, width_scale=width_scale, style=style,
        )
    else:
        raise ValueError(f"Unknown mode {mode!r}")
    if title:
        # title_text (not title=) so a builder's title positioning survives.
        fig.update_layout(title_text=title)
    return fig


# ---------------------------------------------------------------------------
# Data acquisition (cached per item_id across reruns)
# ---------------------------------------------------------------------------

def _extract_cathode_mass_mg(item_dict: dict) -> float | None:
    """Sum all ``quantity`` values on positive-electrode constituents (units: mg)."""
    pos = item_dict.get("positive_electrode") or []
    if not isinstance(pos, list):
        return None
    total = 0.0
    found = False
    for cons in pos:
        if not isinstance(cons, dict):
            continue
        qty = cons.get("quantity")
        if qty is None:
            continue
        try:
            total += float(qty)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def _masses_keyed_by_label(payload: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Return cathode masses (g) keyed by plot label for items that have one."""
    masses_by_id: dict[str, float | None] = st.session_state.get("cathode_masses", {})
    return {
        label: m
        for label, spec in payload.items()
        if (m := masses_by_id.get(spec["item_id"])) is not None
    }


def _ensure_data_for(
    client: DatalabPlotClient, item_ids: list[str], *, force: bool
) -> tuple[int, int, list[str], dict[str, str]]:
    """Make sure parsed echem data is loaded for each ``item_id``.

    Returns ``(cache_hits, cache_misses, skipped, errors)`` where:
      - ``skipped`` lists item_ids that have no cycling files attached.
      - ``errors`` maps item_id → short human-readable error message for
        items that failed to fetch or parse.
    Parsed DataFrames are cached in ``st.session_state['raw_data']`` keyed
    by item_id; items that errored are *not* cached so the next attempt
    can retry from scratch.
    """
    raw: dict[str, pd.DataFrame] = st.session_state.setdefault("raw_data", {})
    cathode_masses: dict[str, float | None] = st.session_state.setdefault("cathode_masses", {})
    skipped: list[str] = []
    errors: dict[str, str] = {}
    hits = misses = 0
    for iid in item_ids:
        if not force and iid in raw:
            continue
        if force:
            client.purge(iid)
        try:
            item_dict = client.client.get_item(item_id=iid)
            mass_mg = _extract_cathode_mass_mg(item_dict)
            cathode_masses[iid] = mass_mg / 1000.0 if mass_mg is not None else None
            results = client.fetch_files_verbose(
                iid, predicate=is_cycling_file, item=item_dict
            )
            if not results:
                skipped.append(iid)
                continue
            for _, status in results:
                if status == "hit":
                    hits += 1
                else:
                    misses += 1
            paths = [p for p, _ in results]
            raw[iid] = load_echem(paths)
        except Exception as exc:
            # Drop any partial result and remember the failure so the caller
            # can deselect the row + show the message.
            logger.warning("Failed to load data for item %s", iid, exc_info=True)
            raw.pop(iid, None)
            errors[iid] = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return hits, misses, skipped, errors


def _raw_keyed_by_label(payload: dict[str, dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """Return raw data re-keyed from item_id to plot label."""
    raw_by_id: dict[str, pd.DataFrame] = st.session_state.get("raw_data", {})
    out: dict[str, pd.DataFrame] = {}
    for label, spec in payload.items():
        iid = spec["item_id"]
        if iid in raw_by_id:
            out[label] = raw_by_id[iid]
    return out


def _render_plot(
    client: DatalabPlotClient,
    payload: dict[str, dict[str, Any]],
    mode: str,
    cycle: int | None,
    title: str,
    width_frac: float,
    height_px: int,
    *,
    x_axis: str = "time",
    y_axis: str = "voltage",
    y2_axis: str = "none",
    color_by_status: bool = False,
    width_scale: float = 1.0,
    style: PlotStyle | None = None,
    force_refresh: bool,
    specific_capacity: bool = False,
) -> None:
    if not payload:
        st.info("Tick rows in the picker to plot.")
        return

    item_ids = [spec["item_id"] for spec in payload.values()]
    need_fetch = force_refresh or any(
        iid not in st.session_state.get("raw_data", {}) for iid in item_ids
    )
    # None suppresses the spinner caption when nothing needs fetching.
    with st.spinner("Fetching files & parsing…" if need_fetch else None):  # type: ignore[arg-type]
        hits, misses, skipped, errors = _ensure_data_for(
            client, item_ids, force=force_refresh
        )

    # Per-item failures: build a new initial DataFrame with those rows
    # deselected and bump the widget version so the editor reflects it. We
    # cannot mutate the current editor's state (Streamlit blocks writes to
    # its key); a version bump replaces the widget entirely.
    if errors:
        broken: dict[str, str] = st.session_state.setdefault("broken_items", {})
        broken.update(errors)
        current = _current_picker_df()
        if not current.empty:
            current = current.reset_index(drop=True)
            current.loc[current["item_id"].isin(errors.keys()), "Select"] = False
            _set_initial(current)
        st.rerun()

    if skipped:
        skip_labels = [
            label for label, spec in payload.items() if spec["item_id"] in skipped
        ]
        st.warning(
            "No cycling files for: " + ", ".join(skip_labels) + " — omitted."
        )
        payload = {k: v for k, v in payload.items() if v["item_id"] not in skipped}
        if not payload:
            return

    raw_by_label = _raw_keyed_by_label(payload)

    masses: dict[str, float] | None = None
    if mode == "summary" and specific_capacity:
        masses = _masses_keyed_by_label(payload)
        skipped_no_mass = [label for label in payload if label not in masses]
        if skipped_no_mass:
            st.warning(
                "Skipped from specific capacity plot — no cathode mass recorded: "
                + ", ".join(skipped_no_mass)
            )

    try:
        fig = _build_plotly(
            payload, raw_by_label, mode, cycle, title or None, height_px,
            x_axis=x_axis, y_axis=y_axis, y2_axis=y2_axis,
            color_by_status=color_by_status, width_scale=width_scale, style=style,
            masses=masses,
        )
    except Exception as exc:
        logger.exception("Plot build failed")
        st.error(f"Plot failed: {exc}")
        return

    # Persist for re-display on subsequent reruns (so checkbox clicks don't
    # have to rebuild + reship the figure).
    st.session_state["last_fig"] = fig
    st.session_state["last_plot"] = {
        "payload": payload, "mode": mode, "cycle": cycle, "title": title,
        "x_axis": x_axis, "y_axis": y_axis, "y2_axis": y2_axis,
        "color_by_status": color_by_status, "width_scale": width_scale,
        "width_frac": width_frac, "height_px": height_px,
        "hits": hits, "misses": misses,
    }

    # Don't render the plot or PNG-export expander here -- main() owns the
    # plot area so the figure persists across reruns at a stable widget key.


def _render_cached_figure() -> None:
    """Re-display the last rendered figure across reruns at a stable widget key.

    This keeps the plot visible while the user toggles checkboxes without
    rebuilding or re-shipping the plotly figure on every click.
    """
    fig = st.session_state.get("last_fig")
    if fig is None:
        return
    cfg = st.session_state.get("last_plot", {})
    width_frac = cfg.get("width_frac", 0.9)
    left_pad = (1.0 - width_frac) / 2
    if left_pad > 0:
        cols = st.columns([left_pad, width_frac, left_pad])
        holder = cols[1]
    else:
        holder = st.container()
    with holder:
        # Stable key — same widget across reruns, so Streamlit/plotly diff
        # rather than re-mount when only ancillary widgets change.
        st.plotly_chart(
            fig, width="stretch", key="main_plot",
            config={
                "displaylogo": False,
                # The modebar camera button exports the live Plotly figure
                # directly — pixel-accurate, no matplotlib re-render, no
                # extra dependency. scale=3 gives a high-res PNG.
                "toImageButtonOptions": {
                    "format": "png",
                    "filename": "datalab_plot",
                    "scale": 3,
                },
            },
        )
    hits, misses = cfg.get("hits", 0), cfg.get("misses", 0)
    if hits + misses:
        st.caption(
            f"Files: {hits}/{hits + misses} cache hit · "
            f"{misses}/{hits + misses} re-downloaded."
        )
