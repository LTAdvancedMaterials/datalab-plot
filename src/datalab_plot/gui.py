"""Streamlit GUI for datalab-plot. Launch via ``datalab-plot gui``."""
from __future__ import annotations

import io
import os
from typing import Any

import matplotlib
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

matplotlib.use("Agg")
import streamlit as st  # noqa: E402

# Streamlit runs this file as a top-level script, so use absolute imports.
from datalab_plot.client import DatalabPlotClient  # noqa: E402
from datalab_plot.parsers.echem import (  # noqa: E402
    compute_dqdv,
    cycle_summary,
    detect_status_column,
    filter_by_cycle,
    is_cycling_file,
    load_echem,
    split_by_status,
    split_half_cycles,
)
from datalab_plot.plots.echem import (  # noqa: E402
    _assign_colors,
    _cumulative_time_hours,
    _normalise_items,
    plot_cycles,
)
from datalab_plot.search import find_cells  # noqa: E402


DEFAULT_URL = "https://datalab.lightningtree.ai/"
PICKER_COLUMNS = ("Select", "item_id", "name", "chemform", "label", "group", "color")

# Common cycler step-type / state values mapped to colours. Anything not in
# the map falls back to a deterministic hash-based tab20 colour, so unknown
# statuses still render distinguishably.
STATUS_COLOR_MAP: dict[str, str] = {
    "CC_Chg":    "#e63946",
    "CV_Chg":    "#f1a208",
    "CCCV_Chg":  "#d62728",
    "CC Chg":    "#e63946",
    "CC_DChg":   "#1f77b4",
    "CV_DChg":   "#56b4e9",
    "CCCV_DChg": "#1a5fb4",
    "CC DChg":   "#1f77b4",
    "Rest":      "#9aa0a6",
    "rest":      "#9aa0a6",
    "R":         "#9aa0a6",
    "Pause":     "#cfd2d6",
    "0":         "#e63946",   # navani: charge
    "1":         "#1f77b4",   # navani: discharge
    "unknown":   "#cccccc",
}


def _status_color(status: str) -> str:
    """Stable colour for a status string. Known ones get a hand-picked hue;
    unknowns fall through to tab20 via a hash so they stay distinguishable."""
    if status in STATUS_COLOR_MAP:
        return STATUS_COLOR_MAP[status]
    import matplotlib.pyplot as _plt
    cmap = _plt.colormaps["tab20"]
    idx = (hash(status) % 20) / 19.0
    return _rgba_to_css(cmap(idx))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _rgba_to_css(c: Any) -> str:
    from matplotlib.colors import to_rgba

    r, g, b, a = to_rgba(c)
    return f"rgba({int(r * 255)}, {int(g * 255)}, {int(b * 255)}, {a:.3f})"


def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    return buf.getvalue()


def _empty_picker_df() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="object") for c in PICKER_COLUMNS})
    df["Select"] = df["Select"].astype(bool)
    return df


# ---------------------------------------------------------------------------
# Picker state: version-bumped key pattern.
#
# Streamlit forbids ALL external writes to st.session_state[<data_editor key>]
# (callbacks, before-first-render, ... it doesn't matter — the check fires the
# next time the widget renders). So the only way to programmatically change a
# data_editor's state is to make it a *different* widget. We do that by
# bumping a version counter that is part of the widget's `key`.
#
#   * picker_initial   -- the DataFrame fed into the editor. Replaced only by
#                         `_set_initial`; never mutated in place.
#   * picker_version   -- integer; bumped by `_set_initial`. Part of the key.
#   * picker_last_edited -- the editor's return value from the previous
#                         render. Bulk handlers use this to preserve the
#                         user's per-row edits (labels/groups/colours) when
#                         constructing the new picker_initial.
# ---------------------------------------------------------------------------

PICKER_KEY_BASE = "picker_editor"


def _picker_widget_key() -> str:
    return f"{PICKER_KEY_BASE}_v{st.session_state.get('picker_version', 0)}"


def _build_initial_df(results: pd.DataFrame, prior_selected: dict[str, dict[str, Any]] | None) -> pd.DataFrame:
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
        rows.append(
            {
                "Select": bool(prev.get("Select", False)),
                "item_id": iid,
                "name": r.get("name", "") or "",
                "chemform": r.get("chemform", "") or "",
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
                "chemform": prev.get("chemform", "") or "",
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


# ---------------------------------------------------------------------------
# Axis machinery for the generic XY mode.
#
# Each axis option resolves to (column_or_callable_for_x_y, axis_label,
# resets_per_half_cycle?). The `resets_per_half_cycle` flag is True for any
# column whose value is reset at half-cycle boundaries (currently just
# `Capacity` in navani's output); when EITHER axis has that flag set, the
# plot needs `split_half_cycles` to avoid drawing fold-back connectors.
# ---------------------------------------------------------------------------

AXIS_OPTIONS = ("time", "voltage", "capacity", "current")

# Map axis key → (column name in raw df, label, resets_per_half_cycle).
# `time` is special — it uses _cumulative_time_hours instead of a raw column.
_AXIS_TABLE: dict[str, tuple[str, str, bool]] = {
    "voltage":  ("Voltage",  "Voltage (V)",     False),
    "capacity": ("Capacity", "Capacity (mAh)",  True),
    "current":  ("Current",  "Current (mA)",    False),
}


def _axis_label(axis: str) -> str:
    if axis == "time":
        return "Time (h)"
    return _AXIS_TABLE[axis][1]


def _axis_resets(axis: str) -> bool:
    if axis == "time":
        return False
    return _AXIS_TABLE[axis][2]


def _axis_series(df: pd.DataFrame, axis: str) -> pd.Series:
    """Return the series for the named axis from a navani DataFrame."""
    if axis == "time":
        return _cumulative_time_hours(df)
    col = _AXIS_TABLE[axis][0]
    return df[col]


# ---------------------------------------------------------------------------
# Plotly figure builders. Each takes the same payload+raw_data shape so a
# single dispatch can serve both Plot-click and live-update reruns.
# ---------------------------------------------------------------------------

def _layout(fig: go.Figure, height: int, title: str | None = None) -> go.Figure:
    fig.update_layout(
        title=title or None,
        template="plotly_white",
        margin=dict(l=60, r=20, t=50 if title else 30, b=80),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
        hovermode="closest",
        height=height,
    )
    return fig


def _plotly_summary(items, raw, colors, height, width_scale: float = 1.0) -> go.Figure:
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Discharge capacity", "Coulombic efficiency"),
    )
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        summ = cycle_summary(raw[label])
        color = _rgba_to_css(colors[label])
        fig.add_trace(
            go.Scatter(
                x=summ["cycle"], y=summ["Discharge_mAh"],
                mode="lines", name=label,
                line=dict(color=color, width=1.6 * width_scale),
                legendgroup=label,
                hovertemplate="cycle %{x}<br>%{y:.1f} mAh<extra>%{fullData.name}</extra>",
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=summ["cycle"], y=100 * summ["CE"],
                mode="lines", name=label,
                line=dict(color=color, width=1.6 * width_scale),
                legendgroup=label, showlegend=False,
                hovertemplate="cycle %{x}<br>%{y:.2f}%<extra>%{fullData.name}</extra>",
            ),
            row=1, col=2,
        )
    fig.update_xaxes(title_text="Cycle number", row=1, col=1)
    fig.update_xaxes(title_text="Cycle number", row=1, col=2)
    fig.update_yaxes(title_text="Discharge capacity (mAh)", row=1, col=1)
    fig.update_yaxes(title_text="Coulombic efficiency (%)", row=1, col=2, range=[90, 102])
    return _layout(fig, height)


# Perceptually uniform sequential colormaps; one per cell, cycled if more
# cells than colormaps are selected.
PER_CELL_CMAPS = ("viridis", "plasma", "inferno", "magma", "cividis")


def _plotly_voltage_capacity(items, raw, colors, height, width_scale: float = 1.0) -> go.Figure:
    """V-Q for every cycle of every cell. Each cell gets its own perceptually
    uniform colormap; cycles are coloured along that gradient (early = light,
    late = dark for viridis-family). The `colors` param is unused here.
    """
    import matplotlib.pyplot as _plt  # local import to keep cold-start light

    fig = go.Figure()
    for cell_idx, it in enumerate(items):
        label = it["label"]
        if label not in raw:
            continue
        df = raw[label]
        if "full cycle" not in df.columns:
            continue
        cycle_ids = sorted({int(c) for c in df["full cycle"].dropna().unique() if c > 0})
        if not cycle_ids:
            continue
        cmap_name = PER_CELL_CMAPS[cell_idx % len(PER_CELL_CMAPS)]
        cmap = _plt.colormaps[cmap_name]
        n = len(cycle_ids)

        # One invisible legend-only trace per cell so the legend stays compact
        # (one entry per cell, coloured with the colormap's mid-point) and the
        # many real per-cycle traces below share its legendgroup for toggling.
        legend_color = _rgba_to_css(cmap(0.5))
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None], mode="lines",
                name=f"{label}  ({cmap_name})",
                line=dict(color=legend_color, width=3 * width_scale),
                legendgroup=label, showlegend=True,
            )
        )

        for j, cid in enumerate(cycle_ids):
            cyc = df[df["full cycle"] == cid]
            x, y = split_half_cycles(cyc, "Capacity", "Voltage")
            frac = j / max(1, n - 1)
            color = _rgba_to_css(cmap(frac))
            fig.add_trace(
                go.Scattergl(
                    x=x, y=y, mode="lines",
                    line=dict(color=color, width=1.0 * width_scale),
                    legendgroup=label, showlegend=False,
                    hovertemplate=(
                        f"<b>{label}</b> · cycle {cid}<br>"
                        "%{x:.2f} mAh<br>%{y:.3f} V<extra></extra>"
                    ),
                )
            )

    fig.update_xaxes(title_text="Capacity (mAh)")
    fig.update_yaxes(title_text="Voltage (V)")
    return _layout(fig, height, title="Voltage vs capacity, all cycles")


def _plotly_dqdv(items, raw, colors, cycle, height, width_scale: float = 1.0) -> go.Figure:
    fig = go.Figure()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        cyc = filter_by_cycle(raw[label], cycle)
        if cyc.empty:
            continue
        diff = compute_dqdv(cyc, mode="dQ/dV")
        if diff.empty:
            continue
        x, y = split_half_cycles(diff, "voltage (V)", "dQ/dV (mA/V)")
        fig.add_trace(
            go.Scatter(
                x=x, y=y,
                mode="lines", name=label, connectgaps=False,
                line=dict(color=_rgba_to_css(colors[label]), width=1.4 * width_scale),
                hovertemplate="%{x:.3f} V<br>%{y:.2f} mA/V<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_xaxes(title_text="Voltage (V)")
    fig.update_yaxes(title_text="dQ/dV (mA/V)")
    return _layout(fig, height, title=f"dQ/dV, cycle {cycle}")


def _plotly_voltage_time(items, raw, colors, height, width_scale: float = 1.0) -> go.Figure:
    fig = go.Figure()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        df = raw[label]
        t = _cumulative_time_hours(df)
        fig.add_trace(
            go.Scatter(
                x=t, y=df["Voltage"],
                mode="lines", name=label,
                line=dict(color=_rgba_to_css(colors[label]), width=1.0 * width_scale),
                hovertemplate="%{x:.2f} h<br>%{y:.3f} V<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_xaxes(title_text="Time (h)")
    fig.update_yaxes(title_text="Voltage (V)")
    return _layout(fig, height, title="Voltage vs time")


def _axis_col_in(df: pd.DataFrame, axis: str) -> tuple[pd.DataFrame, str]:
    """Return ``(df_with_axis_as_column, column_name)``.

    For ``axis="time"`` writes a ``_time_h`` column derived via
    ``_cumulative_time_hours``; for the others returns the existing column
    name. The returned DataFrame may be a shallow copy when a column has
    to be added.
    """
    if axis == "time":
        if "_time_h" not in df.columns:
            df = df.copy()
            df["_time_h"] = _cumulative_time_hours(df).to_numpy()
        return df, "_time_h"
    return df, _AXIS_TABLE[axis][0]


def _plotly_xy(
    items, raw, colors,
    x_axis: str, y_axis: str, y2_axis: str, height: int,
    color_by_status: bool = False,
    width_scale: float = 1.0,
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
    has_y2 = y2_axis and y2_axis != "none"
    needs_gaps = (
        _axis_resets(x_axis)
        or _axis_resets(y_axis)
        or (has_y2 and _axis_resets(y2_axis))
    )
    xlabel = _axis_label(x_axis)
    ylabel = _axis_label(y_axis)
    y2label = _axis_label(y2_axis) if has_y2 else None

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
                _add(dict(
                    x=xs, y=ys, mode="lines",
                    name=f"{label} (no status)",
                    line=dict(color="#777", width=base_w),
                    connectgaps=False,
                    hovertemplate=(
                        f"<b>{label}</b><br>"
                        "%{x:.3f}<br>%{y:.3f}<extra></extra>"
                    ),
                ), secondary=False)
                continue
            for xs, ys, sval in split_by_status(tmp, x_col, axis_col, status_col):
                show = sval not in seen_status
                seen_status.add(sval)
                _add(dict(
                    x=xs, y=ys, mode="lines",
                    name=sval,
                    line=dict(color=_status_color(sval), width=base_w),
                    legendgroup=sval,
                    showlegend=show,
                    hovertemplate=(
                        f"<b>{label}</b> · {sval}<br>"
                        "%{x:.3f}<br>%{y:.3f}<extra></extra>"
                    ),
                ), secondary=False)
    else:
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
            _add(dict(
                x=xs, y=ys, mode="lines",
                name=(f"{label} (left)" if has_y2 else label),
                line=dict(color=cell_color, width=base_w, dash="solid"),
                connectgaps=False,
                legendgroup=label,
                showlegend=True,
                hovertemplate=(
                    f"<b>{label}</b><br>"
                    "%{x:.3f}<br>"
                    f"%{{y:.3f}} {ylabel.split('(')[-1].rstrip(')')}"
                    "<extra></extra>"
                ),
            ), secondary=False)

    # --- Secondary Y axis (always cell-coloured, dashed) ------------------
    if has_y2:
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
            cell_color = _rgba_to_css(colors[label])
            _add(dict(
                x=xs, y=ys, mode="lines",
                name=f"{label} ({y2_axis})",
                line=dict(color=cell_color, width=base_w, dash="dash"),
                connectgaps=False,
                # When colouring by status, the left legend is by step name;
                # the right legend keeps per-cell entries (one per cell) so
                # users can identify which dashed line belongs to which cell.
                legendgroup=(f"y2:{label}" if color_by_status else label),
                showlegend=True if color_by_status else False,
                hovertemplate=(
                    f"<b>{label}</b><br>"
                    "%{x:.3f}<br>"
                    f"%{{y:.3f}} {y2label.split('(')[-1].rstrip(')')}"
                    "<extra></extra>"
                ),
            ), secondary=True)

    # --- Axis labels + title ----------------------------------------------
    fig.update_xaxes(title_text=xlabel)
    if has_y2:
        fig.update_yaxes(title_text=ylabel, secondary_y=False)
        fig.update_yaxes(title_text=y2label, secondary_y=True)
        xtitle = xlabel.split(" (")[0]
        ytitle = ylabel.split(" (")[0]
        y2title = y2label.split(" (")[0]
        suffix = " (by status)" if color_by_status else ""
        title_text = f"{ytitle} & {y2title} vs {xtitle}{suffix}"
    else:
        fig.update_yaxes(title_text=ylabel)
        xtitle = xlabel.split(" (")[0]
        ytitle = ylabel.split(" (")[0]
        suffix = " (by status)" if color_by_status else ""
        title_text = f"{ytitle} vs {xtitle}{suffix}"
    return _layout(fig, height, title=title_text)


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
) -> go.Figure:
    items = _normalise_items(payload)
    colors = _assign_colors(items)
    if mode == "summary":
        fig = _plotly_summary(items, raw, colors, height, width_scale=width_scale)
    elif mode == "voltage_capacity":
        fig = _plotly_voltage_capacity(items, raw, colors, height, width_scale=width_scale)
    elif mode == "dqdv":
        fig = _plotly_dqdv(items, raw, colors, int(cycle or 1), height, width_scale=width_scale)
    elif mode == "voltage_time":
        # Kept for backwards compatibility (cached figures, library parity).
        # In the GUI, V vs t is reached via mode="xy" with x=time, y=voltage.
        fig = _plotly_voltage_time(items, raw, colors, height, width_scale=width_scale)
    elif mode == "xy":
        fig = _plotly_xy(
            items, raw, colors, x_axis, y_axis, y2_axis, height,
            color_by_status=color_by_status, width_scale=width_scale,
        )
    else:
        raise ValueError(f"Unknown mode {mode!r}")
    if title:
        fig.update_layout(title=title)
    return fig


# ---------------------------------------------------------------------------
# Data acquisition (cached per item_id across reruns)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------

def _sidebar_connection() -> DatalabPlotClient | None:
    client: DatalabPlotClient | None = st.session_state.get("client")

    if client is None:
        st.sidebar.subheader("Connect")
        url = st.sidebar.text_input("Datalab URL", value=DEFAULT_URL, key="ui_url")
        api_key = st.sidebar.text_input(
            "API key",
            value=os.environ.get("DATALAB_API_KEY", ""),
            type="password",
            help="Held in memory for this session only.",
            key="ui_api_key",
        )
        if st.sidebar.button("Connect", type="primary", use_container_width=True):
            if not api_key:
                st.sidebar.error("API key is required.")
            else:
                os.environ["DATALAB_API_KEY"] = api_key
                try:
                    c = DatalabPlotClient(url)
                    c.__enter__()
                    info = c.client.get_info()
                    st.session_state["client"] = c
                    st.session_state["server_name"] = (
                        info.get("data", {}).get("attributes", {}).get("name", url)
                    )
                    st.rerun()
                except Exception as exc:
                    st.sidebar.error(f"Connection failed: {exc}")
        return None

    st.sidebar.success(f"✓ {st.session_state.get('server_name', 'connected')}")
    with st.sidebar.expander("Connection", expanded=False):
        st.write(f"**URL** `{client.client.datalab_api_url}`")
        st.write(f"**Cache** `{client.cache_root}`")
        st.caption(
            f"{len(st.session_state.get('raw_data', {}))} cell(s) parsed and in memory."
        )
        if st.button("Forget parsed data", use_container_width=True):
            st.session_state["raw_data"] = {}
            st.rerun()
        if st.button("Sign out", type="secondary", use_container_width=True):
            try:
                client.close()
            finally:
                for k in list(st.session_state.keys()):
                    if k.startswith(f"{PICKER_KEY_BASE}_v") or k in (
                        "client", "server_name", "results", "picker_initial",
                        "picker_version", "picker_last_edited",
                        "raw_data", "last_plot", "last_fig",
                        "last_plot_signature", "broken_items",
                        "ui_preset", "ui_mode",
                        "ui_x_axis", "ui_y_axis", "ui_y2_axis",
                        "ui_cycle", "ui_title", "ui_live",
                        "ui_color_by_status", "ui_width_scale",
                        "ui_plot_width", "ui_plot_height",
                    ):
                        st.session_state.pop(k, None)
                st.rerun()
    return client


def _do_search() -> None:
    """Run a search using ``ui_query`` and the connected client.

    Used both by the Search button (inline) and as the text input's
    ``on_change`` callback so pressing Enter in the search box triggers
    the search without an extra click.
    """
    client: DatalabPlotClient | None = st.session_state.get("client")
    if client is None:
        return
    query = st.session_state.get("ui_query", "") or None
    try:
        df = find_cells(
            query=query,
            item_type=("samples", "cells"),
            limit=300,
            client=client,
        )
        st.session_state["results"] = df
        prior = _current_picker_df()
        prior_by_id = (
            {
                r["item_id"]: r.to_dict()
                for _, r in prior.iterrows()
                if bool(r["Select"])
            }
            if not prior.empty
            else {}
        )
        _set_initial(_build_initial_df(df, prior_by_id))
        st.session_state["_search_error"] = None
    except Exception as exc:
        st.session_state["_search_error"] = str(exc)


def _search_section(client: DatalabPlotClient) -> None:
    """Render the search row. Enter in the text field or clicking Search
    both trigger :func:`_do_search`."""
    cols = st.columns([6, 1])
    cols[0].text_input(
        "Search items",
        value=st.session_state.get("ui_query", ""),
        placeholder="e.g. NMC811 — leave blank to list everything (press Enter to search)",
        label_visibility="collapsed",
        key="ui_query",
        on_change=_do_search,
    )
    if cols[1].button("Search", use_container_width=True):
        _do_search()
    err = st.session_state.pop("_search_error", None)
    if err:
        st.error(f"Search failed: {err}")


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
        if err_cols[1].button("Dismiss", use_container_width=True):
            st.session_state["broken_items"] = {}
            st.rerun()

    # Current selection view = initial + editor diff. Used only for counts /
    # bulk-action targeting; do NOT pass it back into the data_editor's data.
    current = _current_picker_df()
    selected_now = int(current["Select"].sum())
    total = len(initial)

    head = st.columns([4, 1, 1, 1])
    head[0].markdown(f"**{selected_now}** selected of {total}")
    head[1].button(
        "All", on_click=_cb_select_all, help="Select every row",
        use_container_width=True, disabled=total == 0,
    )
    head[2].button(
        "None", on_click=_cb_select_none, help="Clear selection",
        use_container_width=True, disabled=selected_now == 0,
    )
    head[3].button(
        "Invert", on_click=_cb_invert, help="Flip every checkbox",
        use_container_width=True, disabled=total == 0,
    )

    with st.expander("Select a range of rows", expanded=False):
        st.caption(f"Rows are 1-indexed (1–{total}).")
        rcol = st.columns([1, 1, 1, 1])
        rcol[0].number_input("From", min_value=1, max_value=total, value=1, key="range_from")
        rcol[1].number_input("To", min_value=1, max_value=total, value=total, key="range_to")
        rcol[2].button("Check range", on_click=_cb_check_range, use_container_width=True)
        rcol[3].button("Uncheck range", on_click=_cb_uncheck_range, use_container_width=True)

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
        use_container_width=True,
        height=min(500, max(220, 38 * (total + 1))),
        column_config={
            "Select": st.column_config.CheckboxColumn("✓", width="small"),
            "item_id": st.column_config.TextColumn("item_id", disabled=True, width="small"),
            "name": st.column_config.TextColumn("name", disabled=True),
            "chemform": st.column_config.TextColumn("chemform", disabled=True, width="small"),
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


# --- Presets ---------------------------------------------------------------
# A single-select segmented control drives the plot mode + axes. The
# `on_change` callback writes ui_mode / ui_x_axis / ui_y_axis / ui_y2_axis
# according to PRESET_MAP. Writing to those session_state slots is allowed
# because they are *not* widget-owned reserved keys.

PRESET_OPTIONS = (
    "V vs t", "V vs Q", "I vs t", "Q vs t", "V & I vs t",
    "dQ/dV", "Summary", "Custom",
)

# Each entry: (mode, x_axis, y_axis, y2_axis). None means "leave current value".
PRESET_MAP: dict[str, tuple[str, str | None, str | None, str | None]] = {
    "V vs t":     ("xy",                "time", "voltage",  "none"),
    "V vs Q":     ("voltage_capacity",  None,   None,       None),
    "I vs t":     ("xy",                "time", "current",  "none"),
    "Q vs t":     ("xy",                "time", "capacity", "none"),
    "V & I vs t": ("xy",                "time", "voltage",  "current"),
    "dQ/dV":      ("dqdv",              None,   None,       None),
    "Summary":    ("summary",           None,   None,       None),
    # Custom: switch to xy but don't overwrite the user's current axes.
    "Custom":     ("xy",                None,   None,       None),
}


def _apply_preset(preset: str) -> None:
    mode, x, y, y2 = PRESET_MAP[preset]
    st.session_state["ui_mode"] = mode
    if x is not None:
        st.session_state["ui_x_axis"] = x
    if y is not None:
        st.session_state["ui_y_axis"] = y
    if y2 is not None:
        st.session_state["ui_y2_axis"] = y2


def _on_preset_change() -> None:
    preset = st.session_state.get("ui_preset")
    if preset in PRESET_MAP:
        _apply_preset(preset)


def _on_customize_edit() -> None:
    """User touched a widget inside the Customize expander → fall out of any
    named preset and switch to Custom."""
    st.session_state["ui_preset"] = "Custom"


Y2_OPTIONS = ("none", "time", "voltage", "capacity", "current")


def _plot_bar() -> tuple[str, str, str, str, int | None, str, bool, bool, bool, float, float, int]:
    # Seed defaults the first time these widgets render.
    st.session_state.setdefault("ui_preset", "V vs t")
    st.session_state.setdefault("ui_mode", "xy")
    st.session_state.setdefault("ui_x_axis", "time")
    st.session_state.setdefault("ui_y_axis", "voltage")
    st.session_state.setdefault("ui_y2_axis", "none")
    st.session_state.setdefault("ui_color_by_status", False)
    st.session_state.setdefault("ui_width_scale", 2.0)

    # Preset segmented control — single visible row, single source of truth
    # for the named-view selection.
    st.segmented_control(
        "Plot type",
        options=list(PRESET_OPTIONS),
        key="ui_preset",
        on_change=_on_preset_change,
        label_visibility="collapsed",
    )

    mode = st.session_state["ui_mode"]
    is_xy = mode == "xy"

    # Cycle stays inline directly under the preset row, but only when the
    # active mode actually uses it.
    cycle: int | None = None
    if mode == "dqdv":
        cycle = int(
            st.number_input(
                "Cycle", min_value=1, step=1,
                value=st.session_state.get("ui_cycle", 1),
                key="ui_cycle",
                on_change=_on_customize_edit,
            )
        )

    # Customize: axes, title, manual mode override.
    with st.expander("Customize axes & title", expanded=False):
        cols = st.columns([1.2, 1, 1, 1, 3])
        cols[0].selectbox(
            "Mode",
            ["xy", "voltage_capacity", "dqdv", "summary"],
            key="ui_mode",
            on_change=_on_customize_edit,
        )
        cols[1].selectbox(
            "X", AXIS_OPTIONS,
            key="ui_x_axis",
            disabled=not is_xy,
            on_change=_on_customize_edit,
        )
        cols[2].selectbox(
            "Y (left)", AXIS_OPTIONS,
            key="ui_y_axis",
            disabled=not is_xy,
            on_change=_on_customize_edit,
        )
        cols[3].selectbox(
            "Y₂ (right)", Y2_OPTIONS,
            key="ui_y2_axis",
            disabled=not is_xy,
            help="Pick 'none' for a single Y axis; pick any column to overlay it on the right.",
            on_change=_on_customize_edit,
        )
        title = cols[4].text_input("Title (optional)", value="", key="ui_title")
        st.toggle(
            "Colour traces by cycler step (CC_Chg / CV_Chg / Rest …)",
            key="ui_color_by_status",
            disabled=not is_xy,
            help=(
                "When on, each trace is split into segments coloured by the file's "
                "Status / state column. Disables per-cell colouring and the right "
                "Y-axis."
            ),
        )

    # Layout: figure size + line-width slider, tucked out of the way.
    with st.expander("Plot layout", expanded=False):
        lcols = st.columns(3)
        width_pct = lcols[0].slider(
            "Plot width", min_value=40, max_value=100,
            value=st.session_state.get("ui_plot_width", 90),
            step=5, format="%d%%", key="ui_plot_width",
        )
        height_px = lcols[1].slider(
            "Plot height (px)", min_value=320, max_value=900,
            value=st.session_state.get("ui_plot_height", 520),
            step=20, key="ui_plot_height",
        )
        width_scale = lcols[2].slider(
            "Trace width", min_value=0.5, max_value=5.0,
            value=st.session_state.get("ui_width_scale", 2.0),
            step=0.25, key="ui_width_scale",
            help="Multiplies every line width. Useful for screenshots / projectors.",
        )

    # Compact action row. Columns are wide enough that the labels can't
    # collapse to per-letter wrapping on narrow windows. The `Auto` toggle
    # uses a shortened label (help tooltip carries the full description).
    action = st.columns([1, 1, 2], vertical_alignment="center")
    refresh_click = action[0].button(
        "Refresh",
        help="Purge local cache for selected items and re-fetch from the server.",
        use_container_width=True,
    )
    live = action[1].toggle(
        "Auto",
        value=st.session_state.get("ui_live", True),
        help="Auto-refresh: re-render the plot on every selection / preset change.",
        key="ui_live",
    )

    x_axis = st.session_state["ui_x_axis"]
    y_axis = st.session_state["ui_y_axis"]
    y2_axis = st.session_state["ui_y2_axis"]
    color_by_status = bool(st.session_state.get("ui_color_by_status", False))
    return (
        mode, x_axis, y_axis, y2_axis, cycle, title,
        refresh_click, live, color_by_status,
        width_pct / 100.0, float(width_scale), height_px,
    )


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
    force_refresh: bool,
) -> None:
    if not payload:
        st.info("Tick rows in the picker to plot.")
        return

    item_ids = [spec["item_id"] for spec in payload.values()]
    need_fetch = force_refresh or any(
        iid not in st.session_state.get("raw_data", {}) for iid in item_ids
    )
    with st.spinner("Fetching files & parsing…" if need_fetch else None):
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
    try:
        fig = _build_plotly(
            payload, raw_by_label, mode, cycle, title or None, height_px,
            x_axis=x_axis, y_axis=y_axis, y2_axis=y2_axis,
            color_by_status=color_by_status, width_scale=width_scale,
        )
    except Exception as exc:
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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
            fig, use_container_width=True, key="main_plot",
            config={"displaylogo": False},
        )
    hits, misses = cfg.get("hits", 0), cfg.get("misses", 0)
    if hits + misses:
        st.caption(
            f"Files: {hits}/{hits + misses} cache hit · "
            f"{misses}/{hits + misses} re-downloaded."
        )


def _mpl_xy_figure(
    payload, raw, x_axis: str, y_axis: str, y2_axis: str,
    title: str | None, color_by_status: bool = False,
    width_scale: float = 1.0,
):
    """Static matplotlib fallback for the generic XY mode (PNG export).

    Same composition rules as :func:`_plotly_xy`: status colouring can
    coexist with a dual-Y secondary axis, which is always cell-coloured
    and dashed.
    """
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    items = _normalise_items(payload)
    colors = _assign_colors(items)
    xlabel, ylabel = _axis_label(x_axis), _axis_label(y_axis)
    has_y2 = y2_axis and y2_axis != "none"
    y2label = _axis_label(y2_axis) if has_y2 else None
    needs_gaps = (
        _axis_resets(x_axis)
        or _axis_resets(y_axis)
        or (has_y2 and _axis_resets(y2_axis))
    )
    lw = 1.0 * width_scale

    fig, ax = plt.subplots(figsize=(8, 5))
    ax2 = ax.twinx() if has_y2 else None

    def _xy_for(df, axis):
        if needs_gaps:
            tmp, x_col = _axis_col_in(df, x_axis)
            tmp, axis_col = _axis_col_in(tmp, axis)
            return split_half_cycles(tmp, x_col, axis_col)
        return _axis_series(df, x_axis), _axis_series(df, axis)

    # Primary Y: by status if requested, else cell-coloured.
    status_handles: dict[str, Line2D] = {}
    cell_handles: list[tuple[str, Line2D]] = []
    if color_by_status:
        for it in items:
            label = it["label"]
            if label not in raw:
                continue
            df = raw[label]
            status_col = detect_status_column(df)
            tmp, x_col = _axis_col_in(df, x_axis)
            tmp, axis_col = _axis_col_in(tmp, y_axis)
            if status_col is None:
                ax.plot(tmp[x_col], tmp[axis_col], color="#777", lw=lw,
                        label=f"{label} (no status)")
                continue
            for xs, ys, sval in split_by_status(tmp, x_col, axis_col, status_col):
                line, = ax.plot(xs, ys, color=_status_color(sval), lw=lw)
                if sval not in status_handles:
                    status_handles[sval] = line
    else:
        for it in items:
            label = it["label"]
            if label not in raw:
                continue
            df = raw[label]
            x, y = _xy_for(df, y_axis)
            line, = ax.plot(x, y, color=colors[label], lw=lw, label=label)
            cell_handles.append((label, line))

    # Secondary Y: always cell-coloured + dashed.
    if has_y2:
        for it in items:
            label = it["label"]
            if label not in raw:
                continue
            df = raw[label]
            x2, y2 = _xy_for(df, y2_axis)
            ax2.plot(x2, y2, color=colors[label], lw=lw, ls="--")
        ax2.set_ylabel(y2label)

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)

    # Legend assembly — status entries on the left if colouring by status,
    # plus per-cell entries (using a dashed-line marker) on the right when
    # both modes are active so the user can identify which Y2 belongs to
    # which cell.
    legend_handles: list[Line2D] = []
    legend_labels: list[str] = []
    for sval, line in status_handles.items():
        legend_handles.append(line)
        legend_labels.append(sval)
    for label, line in cell_handles:
        legend_handles.append(line)
        legend_labels.append(label)
    if has_y2 and color_by_status:
        # Add one dashed proxy per cell to disambiguate the right axis.
        for it in items:
            label = it["label"]
            if label not in raw:
                continue
            legend_handles.append(
                Line2D([0], [0], color=colors[label], lw=lw, ls="--")
            )
            legend_labels.append(f"{label} ({y2_axis})")
    if legend_handles:
        ax.legend(legend_handles, legend_labels, fontsize=8, loc="best")

    if title:
        ax.set_title(title)
    fig.tight_layout()
    return fig


def _png_export_section(client: DatalabPlotClient) -> None:
    cfg = st.session_state.get("last_plot")
    if not cfg:
        return
    with st.expander("Export static PNG"):
        if st.button("Generate PNG", key="png_btn"):
            try:
                with st.spinner("Rendering PNG via matplotlib…"):
                    if cfg["mode"] == "xy":
                        # plot_cycles has no xy mode; build matplotlib locally
                        # from the in-memory parsed data.
                        raw_by_label = _raw_keyed_by_label(cfg["payload"])
                        mpl_fig = _mpl_xy_figure(
                            cfg["payload"], raw_by_label,
                            cfg.get("x_axis", "time"),
                            cfg.get("y_axis", "voltage"),
                            cfg.get("y2_axis", "none"),
                            cfg.get("title") or None,
                            color_by_status=cfg.get("color_by_status", False),
                            width_scale=cfg.get("width_scale", 1.0),
                        )
                    else:
                        mpl_fig = plot_cycles(
                            cfg["payload"], mode=cfg["mode"], cycle=cfg.get("cycle"),
                            client=client, title=cfg.get("title") or None,
                        )
                st.download_button(
                    "Download PNG",
                    data=_fig_to_png_bytes(mpl_fig),
                    file_name=f"datalab_plot_{cfg['mode']}.png",
                    mime="image/png",
                    key="png_dl",
                )
            except Exception as exc:
                st.error(f"PNG export failed: {exc}")


_GLOBAL_CSS = """
<style>
/* Prevent button labels and widget labels (incl. st.toggle) from wrapping
   into one-character columns on narrow windows. */
.stButton button p,
.stButton button div,
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] label {
    white-space: nowrap !important;
}
</style>
"""


def main() -> None:
    st.set_page_config(page_title="datalab-plot", layout="wide")
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)
    st.title("datalab-plot")

    client = _sidebar_connection()
    if client is None:
        st.info("Connect to a datalab instance from the sidebar to begin.")
        return

    _search_section(client)
    picker_df = _picker_table()
    (
        mode, x_axis, y_axis, y2_axis, cycle, title,
        refresh_click, live, color_by_status,
        width_frac, width_scale, height_px,
    ) = _plot_bar()

    payload = _selected_payload(picker_df)

    # Detect whether the live-mode plot inputs changed since the last render —
    # if not, skip the rebuild even with Auto on. This keeps checkbox toggles
    # snappy when only e.g. the title or width slider moves.
    plot_signature = (
        tuple(sorted((k, v.get("item_id"), v.get("group"), v.get("color"))
                     for k, v in payload.items())),
        mode, x_axis, y_axis, y2_axis, cycle, title,
        color_by_status, width_frac, width_scale, height_px,
    )
    selection_changed = (
        plot_signature != st.session_state.get("last_plot_signature")
    )

    should_render = (
        refresh_click
        or (live and selection_changed and payload)
    )

    if should_render:
        _render_plot(
            client, payload, mode, cycle, title, width_frac, height_px,
            x_axis=x_axis, y_axis=y_axis, y2_axis=y2_axis,
            color_by_status=color_by_status, width_scale=width_scale,
            force_refresh=refresh_click,
        )
        st.session_state["last_plot_signature"] = plot_signature

    # Always re-display the cached figure (kept stable by key="main_plot"),
    # so checkbox toggles when Auto-refresh is off don't blank the plot.
    if "last_fig" in st.session_state:
        _render_cached_figure()
        _png_export_section(client)
    elif payload:
        st.caption("Tick rows and pick a plot type — the figure renders automatically.")
    else:
        st.caption("Tick rows in the picker, then click **Plot**.")


if __name__ == "__main__":
    main()
