"""Plotly figure builders, layout styling, and figure-payload data acquisition.

The figure builders consume :mod:`datalab_plot.series` for all data
preparation — the per-cycle iteration / dQ-dV / summary logic lives there
and is shared with the matplotlib renderers in :mod:`datalab_plot.plots.echem`.

This module is pure (no Dash, no Streamlit imports). The Dash GUI in
:mod:`datalab_plot.gui_dash` consumes ``build_figure_for_payload`` from here
to turn a staged-items payload + options into a Plotly figure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from datalab_plot.client import DatalabPlotClient
from datalab_plot.parsers.echem import (
    cycle_summary,
    detect_status_column,
    is_cycling_file,
    load_echem,
    split_by_status,
    split_half_cycles,
)
from datalab_plot.plot_constants import PlotStyle
from datalab_plot.plot_helpers import (
    _axis_col_in,
    _axis_label,
    _axis_resets,
    _axis_series,
    _desaturate_css,
    _mpl_colorscale,
    _rgba_to_css,
    _status_color,
)
from datalab_plot.plots.echem import _assign_colors, _normalise_items
from datalab_plot.search import extract_cathode_mass_mg
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


def _display_name(it: dict) -> str:
    """Legend / trace name: 'My Label  [CEL-085]' or just 'CEL-085' when identical."""
    label = it["label"]
    iid = it.get("item_id", "")
    return label if (label == iid or not iid) else f"{label}  [{iid}]"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _layout(
    fig: go.Figure, height: int | None, title: str | None = None,
    style: PlotStyle | None = None, *,
    secondary_y: bool = False,
    n_legend_items: int = 0,
    template: str = "plotly_white",
) -> go.Figure:
    style = style or PlotStyle()
    # Cap the "below" legend at 160 px; a scrollbar appears beyond that so
    # the legend never extends past the figure boundary regardless of item count.
    # y=-0.30 pushes the legend's top anchor below the x-axis title (≈ 0.22
    # paper units above the axis baseline isn't enough when the title itself
    # takes ~0.06–0.08 paper units). The bottom margin scales with the capped
    # number of rows; assume 3 entries/row (long cell names), max 6 rows before
    # the scrollbar takes over.
    _LEGEND_MAX_H = 160  # px; scrollbar kicks in above this
    _PX_PER_ROW = 26
    _ITEMS_PER_ROW = 3
    legend_cfg = {
        "below": dict(
            orientation="h", yanchor="top", y=-0.30, xanchor="center", x=0.5,
            maxheight=_LEGEND_MAX_H,
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
    if style.legend_mode == "below" and n_legend_items > 0:
        rows = min(6, max(1, (n_legend_items + _ITEMS_PER_ROW - 1) // _ITEMS_PER_ROW))
        b_below = 80 + rows * _PX_PER_ROW
    else:
        b_below = 90
    # Sanitise title: reject empty strings and the JS-serialised "undefined".
    # Use title_text (not title=) so an empty string actively clears any stale
    # title that may be set on a cached Figure object from a previous render.
    clean_title = title if title and title.lower() != "undefined" else ""
    # When `height` is None we omit it so Plotly inherits its container
    # height (driven by the `.ui-plot-graph` CSS rule on the dcc.Graph
    # wrapper). When a number is passed (e.g. from legacy callers /
    # tests), use it as the explicit figure height.
    layout_kwargs: dict[str, Any] = dict(
        title_text=clean_title,
        template=template,
        margin=dict(
            l=60, r=20, t=50 if clean_title else 30,
            b=b_below if style.legend_mode == "below" else 40,
        ),
        showlegend=(style.legend_mode != "none"),
        legend=legend_cfg,
        hovermode="closest",
        # layout.font is the global default; tick labels inherit it. Axis
        # titles and the plot title carry their own font objects, so set
        # those explicitly too — otherwise the Text-size control only
        # resizes tick labels and the legend.
        font=dict(size=style.font_size),
        title_font=dict(size=style.font_size + 3),
    )
    if height:
        layout_kwargs["height"] = height
    fig.update_layout(**layout_kwargs)
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

def _cycle_dtick(max_cycle: int) -> int:
    """Pick a tick spacing for the cycle-number x-axis that keeps every
    tick at an integer position (no 0.5 / 1.5 / 2.5 visible) while staying
    in a readable 6–15 ticks across the visible range.

    Used by ``_plotly_summary`` (the only builder with cycle number on x).
    Combined with ``tickmode="linear"`` + ``tick0=0`` it guarantees
    integer-only ticks at every zoom level.
    """
    if max_cycle <= 15:
        return 1
    if max_cycle <= 30:
        return 2
    if max_cycle <= 80:
        return 5
    if max_cycle <= 200:
        return 10
    if max_cycle <= 500:
        return 25
    if max_cycle <= 1000:
        return 50
    return 100


def _plotly_summary(
    items, raw, colors, height, width_scale: float = 1.0,
    style: PlotStyle | None = None,
    masses: dict[str, float] | None = None,
    template: str = "plotly_white",
) -> list[tuple[str, go.Figure]]:
    """Return three (tab_title, figure) pairs: discharge, charge, CE."""
    specific = bool(masses)
    cap_unit = "mAh/g" if specific else "mAh"
    dch_label = f"Discharge capacity ({cap_unit})"
    chg_label = f"Charge capacity ({cap_unit})"

    fig_dch = go.Figure()
    fig_chg = go.Figure()
    fig_ce = go.Figure()

    all_cycles: list[int] = []
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        mass_g = masses.get(label) if masses else None
        s = summary_series(raw[label], mass_g=mass_g)
        if specific and s.discharge_mah_g is None:
            continue
        all_cycles.extend(int(c) for c in s.cycle)
        dch_y = s.discharge_mah_g if specific else s.discharge_mah
        chg_y = s.charge_mah_g if specific else s.charge_mah
        color = _rgba_to_css(colors[label])
        w = 1.6 * width_scale
        hover_cap = (
            f"cycle %{{x}}<br>%{{y:.2f}} {cap_unit}<extra>%{{fullData.name}}</extra>"
        )
        dname = _display_name(it)
        fig_dch.add_trace(go.Scatter(
            x=s.cycle, y=dch_y, **_trace_style(style, color, w),
            name=dname, hovertemplate=hover_cap,
        ))
        fig_chg.add_trace(go.Scatter(
            x=s.cycle, y=chg_y, **_trace_style(style, color, w),
            name=dname, hovertemplate=hover_cap,
        ))
        fig_ce.add_trace(go.Scatter(
            x=s.cycle, y=s.ce_percent, **_trace_style(style, color, w),
            name=dname,
            hovertemplate="cycle %{x}<br>%{y:.2f}%<extra>%{fullData.name}</extra>",
        ))

    if all_cycles:
        n_max = int(max(all_cycles))
        x_range = [0.5, n_max + 0.5]
        dtick = _cycle_dtick(n_max)
    else:
        x_range = None
        dtick = 1

    for fig, y_title in (
        (fig_dch, dch_label),
        (fig_chg, chg_label),
        (fig_ce, "Coulombic efficiency (%)"),
    ):
        # tickmode="linear" + tick0=0 + integer dtick guarantees ticks at
        # integer positions only (no 0.5 / 1.5 / 2.5). tickformat="d"
        # additionally strips any decimal in the label rendering.
        fig.update_xaxes(
            title_text="Cycle number",
            range=x_range,
            tickmode="linear",
            tick0=0,
            dtick=dtick,
            tickformat="d",
        )
        fig.update_yaxes(title_text=y_title)
        _layout(fig, height, style=style, n_legend_items=len(items),
                template=template)

    return [
        ("Discharge capacity", fig_dch),
        ("Charge capacity", fig_chg),
        ("Coulombic efficiency", fig_ce),
    ]


def _plotly_voltage_capacity(items, raw, colors, height, width_scale: float = 1.0,
                             style: PlotStyle | None = None,
                             template: str = "plotly_white") -> go.Figure:
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
        dname = _display_name(it)
        legend_color = _rgba_to_css(cmap(0.5))
        legend_w = 3 * width_scale
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None],
                **_trace_style(style, legend_color, legend_w),
                name=f"{dname}  ({cmap_name})",
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
                        f"<b>{dname}</b> · cycle {t.cycle_id}<br>"
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
                            title=dict(text=f"{dname}<br>cycle", side="right"),
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
    fig = _layout(fig, height, title="Voltage vs capacity, all cycles", style=style,
                  n_legend_items=len(items), template=template)
    if n_colorbars:
        # Widen the right margin so the colorbars don't overlap the plot.
        fig.update_layout(margin=dict(r=20 + 90 * n_colorbars))
    return fig


def _plotly_dqdv(items, raw, colors, cycle, height, width_scale: float = 1.0,
                 style: PlotStyle | None = None,
                 template: str = "plotly_white") -> go.Figure:
    fig = go.Figure()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        color = _rgba_to_css(colors[label])
        dname = _display_name(it)
        for t in dqdv_series(raw[label], cycle):
            fig.add_trace(
                go.Scatter(
                    x=t.x, y=t.y,
                    **_trace_style(style, color, 1.4 * width_scale),
                    name=dname, connectgaps=False,
                    hovertemplate="%{x:.3f} V<br>%{y:.2f} mA/V<extra>%{fullData.name}</extra>",
                )
            )
    fig.update_xaxes(title_text="Voltage (V)")
    fig.update_yaxes(title_text="dQ/dV (mA/V)")
    return _layout(fig, height, title=f"dQ/dV, cycle {cycle}", style=style,
                   n_legend_items=len(items), template=template)


def _plotly_voltage_time(items, raw, colors, height, width_scale: float = 1.0,
                         style: PlotStyle | None = None,
                         template: str = "plotly_white") -> go.Figure:
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
                name=_display_name(it),
                hovertemplate="%{x:.2f} h<br>%{y:.3f} V<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_xaxes(title_text="Time (h)")
    fig.update_yaxes(title_text="Voltage (V)")
    return _layout(fig, height, title="Voltage vs time", style=style,
                   n_legend_items=len(items), template=template)


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
        dname = _display_name(it)
        add({
            "x": xs, "y": ys,
            **_trace_style(style, cell_color, base_w),
            "name": (f"{dname} (left)" if has_y2 else dname),
            "connectgaps": False,
            "legendgroup": label,
            "showlegend": True,
            "hovertemplate": (
                f"<b>{dname}</b><br>"
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
        dname = _display_name(it)
        add({
            "x": xs, "y": ys,
            **_trace_style(style, cell_color, base_w, dash="dash", secondary=True),
            "name": f"{dname} ({y2_axis})",
            "connectgaps": False,
            # When colouring by status, the left legend is by step name;
            # the right legend keeps per-cell entries (one per cell) so
            # users can identify which dashed line belongs to which cell.
            "legendgroup": (f"y2:{label}" if color_by_status else label),
            "showlegend": True if color_by_status else False,
            "hovertemplate": (
                f"<b>{dname}</b><br>"
                "%{x:.3f}<br>"
                f"%{{y:.3f}} {y2label.split('(')[-1].rstrip(')')}"
                "<extra></extra>"
            ),
        }, secondary=True)


def _plotly_xy(
    items, raw, colors,
    x_axis: str, y_axis: str, y2_axis: str, height: int | None,
    color_by_status: bool = False,
    width_scale: float = 1.0,
    style: PlotStyle | None = None,
    template: str = "plotly_white",
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
    fig = _layout(fig, height, title=title_text, style=style, secondary_y=has_y2,
                  n_legend_items=len(items), template=template)
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
    height: int | None,
    x_axis: str = "time",
    y_axis: str = "voltage",
    y2_axis: str = "none",
    color_by_status: bool = False,
    width_scale: float = 1.0,
    style: PlotStyle | None = None,
    masses: dict[str, float] | None = None,
    template: str = "plotly_white",
) -> go.Figure | list[tuple[str, go.Figure]]:
    items = _normalise_items(payload)
    colors = _assign_colors(items)
    if mode == "summary":
        return _plotly_summary(items, raw, colors, height,
                               width_scale=width_scale, style=style,
                               masses=masses, template=template)
    elif mode == "voltage_capacity":
        fig = _plotly_voltage_capacity(items, raw, colors, height,
                                       width_scale=width_scale, style=style,
                                       template=template)
    elif mode == "dqdv":
        fig = _plotly_dqdv(items, raw, colors, int(cycle or 1), height,
                           width_scale=width_scale, style=style,
                           template=template)
    elif mode == "voltage_time":
        # Kept for backwards compatibility (cached figures, library parity).
        # In the GUI, V vs t is reached via mode="xy" with x=time, y=voltage.
        fig = _plotly_voltage_time(items, raw, colors, height,
                                   width_scale=width_scale, style=style,
                                   template=template)
    elif mode == "xy":
        fig = _plotly_xy(
            items, raw, colors, x_axis, y_axis, y2_axis, height,
            color_by_status=color_by_status, width_scale=width_scale, style=style,
            template=template,
        )
    else:
        raise ValueError(f"Unknown mode {mode!r}")
    if title and title.lower() != "undefined":
        fig.update_layout(title_text=title)
    return fig


# ---------------------------------------------------------------------------
# Data acquisition (cached per item_id across reruns)
# ---------------------------------------------------------------------------

def _masses_keyed_by_label(
    payload: dict[str, dict[str, Any]],
    cathode_masses: dict[str, float | None],
) -> dict[str, float]:
    """Return cathode masses (g) keyed by plot label for items that have one."""
    return {
        label: m
        for label, spec in payload.items()
        if (m := cathode_masses.get(spec["item_id"])) is not None
    }


def _ensure_data_for(
    client: DatalabPlotClient,
    item_ids: list[str],
    *,
    force: bool,
    raw_data: dict[str, pd.DataFrame],
    cathode_masses: dict[str, float | None],
) -> tuple[int, int, list[str], dict[str, str]]:
    """Make sure parsed echem data is loaded for each ``item_id``.

    Mutates ``raw_data`` and ``cathode_masses`` in place. Returns
    ``(cache_hits, cache_misses, skipped, errors)`` where:
      - ``skipped`` lists item_ids that have no cycling files attached.
      - ``errors`` maps item_id → short human-readable error message for
        items that failed to fetch or parse.
    Items that errored are *not* cached so the next attempt can retry
    from scratch.
    """
    skipped: list[str] = []
    errors: dict[str, str] = {}
    hits = misses = 0
    for iid in item_ids:
        if not force and iid in raw_data:
            continue
        if force:
            client.purge(iid)
        try:
            item_dict = client.client.get_item(item_id=iid)
            mass_mg = extract_cathode_mass_mg(item_dict)
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
            raw_data[iid] = load_echem(paths)
        except Exception as exc:
            # Drop any partial result and remember the failure so the caller
            # can deselect the row + show the message.
            logger.warning("Failed to load data for item %s", iid, exc_info=True)
            raw_data.pop(iid, None)
            errors[iid] = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return hits, misses, skipped, errors


def _raw_keyed_by_label(
    payload: dict[str, dict[str, Any]],
    raw_data: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Return raw data re-keyed from item_id to plot label."""
    out: dict[str, pd.DataFrame] = {}
    for label, spec in payload.items():
        iid = spec["item_id"]
        if iid in raw_data:
            out[label] = raw_data[iid]
    return out


def _build_capacity_table(
    summaries: dict[str, pd.DataFrame],
    cycle_n: int,
    item_ids: dict[str, str] | None = None,
) -> pd.DataFrame:
    """One-row-per-cell table of charge/discharge capacity and CE at ``cycle_n``."""
    rows: list[dict[str, Any]] = []
    for label, summ in summaries.items():
        match = summ[summ["cycle"] == cycle_n]
        iid = item_ids.get(label, "") if item_ids else ""
        if match.empty:
            rows.append({
                "Cell": label, "Item ID": iid,
                "Charge (mAh)": None, "Discharge (mAh)": None, "CE (%)": None,
            })
        else:
            r = match.iloc[0]
            ce = round(float(r["CE"]) * 100, 2) if pd.notna(r["CE"]) else None
            rows.append({
                "Cell": label,
                "Item ID": iid,
                "Charge (mAh)": round(float(r["Charge_mAh"]), 3),
                "Discharge (mAh)": round(float(r["Discharge_mAh"]), 3),
                "CE (%)": ce,
            })
    return pd.DataFrame(rows)


@dataclass
class FigureResult:
    """Pure output of ``build_figure_for_payload`` — everything the UI needs.

    Holds all info needed to display the result, including per-item warnings,
    errors, and cache stats. Item-level failures don't raise — they appear in
    ``errors``, and the caller decides what to do (e.g. deselect the row).
    """
    fig: go.Figure | list[tuple[str, go.Figure]] | None = None
    last_plot: dict[str, Any] = field(default_factory=dict)
    cycle_summaries: dict[str, pd.DataFrame] | None = None
    hits: int = 0
    misses: int = 0
    skipped_labels: list[str] = field(default_factory=list)
    skipped_no_mass: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    payload: dict[str, dict[str, Any]] = field(default_factory=dict)
    error_message: str | None = None


def build_figure_for_payload(
    client: DatalabPlotClient,
    payload: dict[str, dict[str, Any]],
    mode: str,
    cycle: int | None,
    title: str,
    width_frac: float,
    height_px: int | None,
    *,
    x_axis: str = "time",
    y_axis: str = "voltage",
    y2_axis: str = "none",
    color_by_status: bool = False,
    width_scale: float = 1.0,
    style: PlotStyle | None = None,
    force_refresh: bool,
    specific_capacity: bool = False,
    raw_data: dict[str, pd.DataFrame],
    cathode_masses: dict[str, float | None],
    theme: str = "light",
) -> FigureResult:
    """Fetch + parse + build a Plotly figure for ``payload``, no UI framework.

    Mutates the ``raw_data`` / ``cathode_masses`` caches in place. The
    Streamlit and Dash front-ends both call this and translate the result
    into their own widget surface. ``theme`` selects the Plotly template
    (``"dark"`` → ``plotly_dark``; anything else → ``plotly_white``).
    """
    template = "plotly_dark" if theme == "dark" else "plotly_white"
    if not payload:
        return FigureResult()

    item_ids = [spec["item_id"] for spec in payload.values()]
    hits, misses, skipped, errors = _ensure_data_for(
        client, item_ids, force=force_refresh,
        raw_data=raw_data, cathode_masses=cathode_masses,
    )

    skip_labels: list[str] = []
    if skipped:
        skip_labels = [
            label for label, spec in payload.items() if spec["item_id"] in skipped
        ]
        payload = {k: v for k, v in payload.items() if v["item_id"] not in skipped}

    if not payload:
        return FigureResult(
            hits=hits, misses=misses, skipped_labels=skip_labels, errors=errors,
        )

    raw_by_label = _raw_keyed_by_label(payload, raw_data)

    masses: dict[str, float] | None = None
    skipped_no_mass: list[str] = []
    if mode == "summary" and specific_capacity:
        masses = _masses_keyed_by_label(payload, cathode_masses)
        skipped_no_mass = [label for label in payload if label not in masses]

    try:
        fig = _build_plotly(
            payload, raw_by_label, mode, cycle, title or None, height_px,
            x_axis=x_axis, y_axis=y_axis, y2_axis=y2_axis,
            color_by_status=color_by_status, width_scale=width_scale, style=style,
            masses=masses, template=template,
        )
    except Exception as exc:
        logger.exception("Plot build failed")
        return FigureResult(
            hits=hits, misses=misses, skipped_labels=skip_labels,
            skipped_no_mass=skipped_no_mass, errors=errors,
            payload=payload, error_message=f"{type(exc).__name__}: {exc}",
        )

    cycle_summaries: dict[str, pd.DataFrame] | None = None
    if mode == "summary":
        cycle_summaries = {
            label: cycle_summary(raw_by_label[label])
            for label in payload
            if label in raw_by_label
        }

    last_plot = {
        "payload": payload, "mode": mode, "cycle": cycle, "title": title,
        "x_axis": x_axis, "y_axis": y_axis, "y2_axis": y2_axis,
        "color_by_status": color_by_status, "width_scale": width_scale,
        "width_frac": width_frac, "height_px": height_px,
        "hits": hits, "misses": misses,
        # Used by plotting_panel._render_plot's gate to detect
        # theme switches even when the auto-refresh option is off.
        "theme": theme,
    }

    return FigureResult(
        fig=fig, last_plot=last_plot, cycle_summaries=cycle_summaries,
        hits=hits, misses=misses, skipped_labels=skip_labels,
        skipped_no_mass=skipped_no_mass, errors=errors, payload=payload,
    )
_PLOTLY_CONFIG = {
    "displaylogo": False,
    # Let Plotly reflow to the container size on window resize (also
    # dispatched by the column-divider drag handler in app.py). Combined
    # with figure.layout.height=None this gives a viewport-responsive plot.
    "responsive": True,
    # The modebar camera button exports the live Plotly figure directly —
    # pixel-accurate, no matplotlib re-render. scale=3 gives a high-res PNG.
    "toImageButtonOptions": {
        "format": "png",
        "filename": "datalab_plot",
        "scale": 3,
    },
}
