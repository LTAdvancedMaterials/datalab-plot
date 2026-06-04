"""matplotlib plots for electrochemical cycling data."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure

from ..client import DatalabPlotClient, _resolve_client
from ..parsers.echem import is_cycling_file, load_echem
from ..series import (
    cycle_cmap,
    dqdv_series,
    summary_series,
    voltage_capacity_series,
    voltage_time_series,
)

ItemsArg = list[str] | Mapping[str, str] | Mapping[str, Mapping[str, Any]]


def _normalise_items(items: ItemsArg) -> list[dict[str, Any]]:
    """Coerce the ``items`` argument to a list of {label, item_id, group, color} dicts."""
    out: list[dict[str, Any]] = []
    if isinstance(items, list):
        for x in items:
            out.append({"label": x, "item_id": x, "group": None, "color": None})
        return out
    if isinstance(items, Mapping):
        for label, v in items.items():
            if isinstance(v, str):
                out.append({"label": label, "item_id": v, "group": None, "color": None})
            elif isinstance(v, Mapping):
                if "item_id" not in v:
                    raise ValueError(f"items[{label!r}] dict must include 'item_id'")
                out.append(
                    {
                        "label": label,
                        "item_id": v["item_id"],
                        "group": v.get("group"),
                        "color": v.get("color"),
                    }
                )
            else:
                raise TypeError(f"items[{label!r}] must be a string or a dict")
        return out
    raise TypeError("items must be a list of item_ids or a dict")


_DEFAULT_GROUP_CMAPS = ("Blues", "Oranges", "Greens", "Purples", "Reds", "Greys")


def _assign_colors(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick a colour for each item.

    Items with explicit ``color`` are honoured. Items with a ``group`` are
    coloured from a per-group sequential colormap (Blues / Oranges / Greens
    / …). Items with neither group nor colour get distinct tab10 colours.
    """
    colors: dict[str, Any] = {}
    grouped: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []
    for it in items:
        if it["color"] is not None:
            colors[it["label"]] = it["color"]
            continue
        if it["group"]:
            grouped.setdefault(it["group"], []).append(it)
        else:
            ungrouped.append(it)
    for group_idx, (_, members) in enumerate(grouped.items()):
        cmap = plt.colormaps[_DEFAULT_GROUP_CMAPS[group_idx % len(_DEFAULT_GROUP_CMAPS)]]
        n = max(1, len(members))
        for i, m in enumerate(members):
            frac = 0.4 + 0.55 * i / max(1, n - 1)
            colors[m["label"]] = cmap(frac)
    tab10 = plt.colormaps["tab10"]
    for i, m in enumerate(ungrouped):
        colors[m["label"]] = tab10(i % 10)
    return colors


def _load_for_items(
    items: list[dict[str, Any]], client: DatalabPlotClient
) -> dict[str, pd.DataFrame]:
    """Download + parse cycling data for each item. Returns label → raw_df."""
    data: dict[str, pd.DataFrame] = {}
    for it in items:
        paths = client.fetch_files(it["item_id"], predicate=is_cycling_file)
        if not paths:
            raise RuntimeError(
                f"No cycling files found for item {it['item_id']!r} "
                f"(label {it['label']!r})"
            )
        data[it["label"]] = load_echem(paths)
    return data


def plot_cycles(
    items: ItemsArg,
    *,
    mode: str = "summary",
    cycle: int | None = None,
    client: DatalabPlotClient | None = None,
    ax: plt.Axes | None = None,
    title: str | None = None,
) -> Figure:
    """Overlay cycling data from multiple cells.

    Parameters
    ----------
    items
        Cells to plot. One of:

        - ``list[str]`` of item_ids (labels = ids, auto-coloured),
        - ``dict[label, item_id]``,
        - ``dict[label, {item_id, group?, color?}]``.
    mode
        ``"summary"`` (default): two-panel discharge capacity & CE vs cycle.
        ``"voltage_capacity"``: V vs Q for every cycle of every cell. Each
            cell gets its own perceptually uniform colormap; cycles are
            coloured along the gradient. ``cycle`` is ignored.
        ``"dqdv"``: dQ/dV vs V at ``cycle``, one trace per cell.
        ``"voltage_time"``: V vs time (h), one trace per cell.
    cycle
        Required for ``dqdv``. Ignored for the other modes.
    client
        Optional pre-opened :class:`DatalabPlotClient`. If omitted, one is
        constructed from ``$DATALAB_URL`` for the duration of the call.
    ax
        Used only in modes that draw on a single Axes. Ignored for ``summary``
        (which always creates its own two-panel figure).
    title
        Overall figure title.
    """
    item_list = _normalise_items(items)
    colors = _assign_colors(item_list)
    c, owns = _resolve_client(client)
    try:
        raw = _load_for_items(item_list, c)
    finally:
        if owns:
            c.close()

    if mode == "summary":
        return _plot_summary(item_list, raw, colors, title)
    if mode == "voltage_capacity":
        # `cycle` is intentionally ignored: V-Q overlays every cycle for every
        # cell, with a different perceptually uniform colormap per cell.
        return _plot_voltage_capacity(item_list, raw, ax, title)
    if mode == "dqdv":
        if cycle is None:
            raise ValueError("mode='dqdv' requires cycle=<int>")
        return _plot_dqdv(item_list, raw, colors, cycle, ax, title)
    if mode == "voltage_time":
        return _plot_voltage_time(item_list, raw, colors, ax, title)
    raise ValueError(f"Unknown mode {mode!r}")


def _plot_summary(
    items: list[dict],
    raw: dict[str, pd.DataFrame],
    colors: dict[str, Any],
    title: str | None,
) -> Figure:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    handles: list = []
    labels: list[str] = []
    for it in items:
        label = it["label"]
        s = summary_series(raw[label])
        line, = ax1.plot(
            s.cycle, s.discharge_mah, color=colors[label], lw=1.4, label=label
        )
        ax2.plot(s.cycle, s.ce_percent, color=colors[label], lw=1.4)
        handles.append(line)
        labels.append(label)
    ax1.set_xlabel("Cycle number")
    ax1.set_ylabel("Discharge capacity (mAh)")
    ax1.set_title("Discharge capacity")
    ax1.grid(alpha=0.3)
    ax2.set_xlabel("Cycle number")
    ax2.set_ylabel("Coulombic efficiency (%)")
    ax2.set_title("Coulombic efficiency")
    ax2.set_ylim(90, 102)
    ax2.grid(alpha=0.3)
    # Single legend below both panels, wrapped to a sensible number of columns.
    ncol = min(max(1, len(labels)), 6)
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=ncol,
        fontsize=8,
        frameon=False,
        bbox_to_anchor=(0.5, -0.02),
    )
    if title:
        fig.suptitle(title, fontsize=13)
    # Reserve room at the bottom for the shared legend (and at the top for the title).
    legend_rows = (len(labels) + ncol - 1) // ncol
    bottom = 0.06 + 0.03 * legend_rows
    top = 0.92 if title else 0.97
    fig.tight_layout(rect=(0, bottom, 1, top))
    return fig


def _plot_voltage_capacity(
    items: list[dict],
    raw: dict[str, pd.DataFrame],
    ax: plt.Axes | None,
    title: str | None,
) -> Figure:
    """Plot V vs Q for every cycle of every cell.

    Each cell gets a distinct single-hue colour-to-dark gradient (orange
    first by default). Late cycle = dark / saturated end. The legend has
    one entry per cell using the colormap's mid-point colour as a
    representative swatch.
    """
    from matplotlib.lines import Line2D

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 6))
    else:
        # ax belongs to a top-level Figure (never a SubFigure) in this app.
        fig = ax.figure  # type: ignore[assignment]

    legend_handles: list[Line2D] = []
    legend_labels: list[str] = []
    for cell_idx, it in enumerate(items):
        label = it["label"]
        traces = voltage_capacity_series(raw[label])
        if not traces:
            continue
        cmap, cmap_name = cycle_cmap(cell_idx)
        for t in traces:
            ax.plot(t.x, t.y, color=cmap(t.frac), lw=0.9)
        legend_handles.append(Line2D([0], [0], color=cmap(0.5), lw=3))
        legend_labels.append(f"{label}  ({cmap_name})")

    ax.set_xlabel("Capacity (mAh)")
    ax.set_ylabel("Voltage (V)")
    ax.set_title(title or "Voltage vs capacity — all cycles")
    ax.grid(alpha=0.3)
    if legend_handles:
        ax.legend(legend_handles, legend_labels, fontsize=8, loc="best")
    fig.tight_layout()
    return fig


def _plot_dqdv(
    items: list[dict],
    raw: dict[str, pd.DataFrame],
    colors: dict[str, Any],
    cycle: int,
    ax: plt.Axes | None,
    title: str | None,
) -> Figure:
    if ax is None:
        fig, ax = plt.subplots(figsize=(7, 5))
    else:
        # ax belongs to a top-level Figure (never a SubFigure) in this app.
        fig = ax.figure  # type: ignore[assignment]
    for it in items:
        label = it["label"]
        for t in dqdv_series(raw[label], cycle):
            ax.plot(t.x, t.y, label=label, color=colors[label], lw=1.2)
    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("dQ/dV (mA/V)")
    ax.set_title(title or f"dQ/dV, cycle {cycle}")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    return fig


def _plot_voltage_time(
    items: list[dict],
    raw: dict[str, pd.DataFrame],
    colors: dict[str, Any],
    ax: plt.Axes | None,
    title: str | None,
) -> Figure:
    if ax is None:
        fig, ax = plt.subplots(figsize=(9, 5))
    else:
        # ax belongs to a top-level Figure (never a SubFigure) in this app.
        fig = ax.figure  # type: ignore[assignment]
    for it in items:
        label = it["label"]
        s = voltage_time_series(raw[label])
        ax.plot(s.x, s.y, label=label, color=colors[label], lw=0.8)
    ax.set_xlabel("Time (h)")
    ax.set_ylabel("Voltage (V)")
    ax.set_title(title or "Voltage vs time")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    return fig


def plot_cell(
    item_id: str,
    *,
    mode: str = "voltage_time",
    cycles: int | list[int] | None = None,
    client: DatalabPlotClient | None = None,
    ax: plt.Axes | None = None,
    title: str | None = None,
) -> Figure:
    """Single-cell deep dive.

    Modes:
      - ``"voltage_time"``: V vs time, all data.
      - ``"voltage_capacity"``: V vs Q per cycle. Coloured by cycle index.
        Pass ``cycles=`` to restrict.
      - ``"dqdv"``: dQ/dV per cycle (``cycles=`` required).
      - ``"summary"``: same as :func:`plot_cycles` with a single cell.
    """
    if mode == "summary":
        return plot_cycles([item_id], mode="summary", client=client, title=title)

    c, owns = _resolve_client(client)
    try:
        paths = c.fetch_files(item_id, predicate=is_cycling_file)
    finally:
        if owns:
            c.close()
    if not paths:
        raise RuntimeError(f"No cycling files for item {item_id!r}")
    df = load_echem(paths)

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 5))
    else:
        # ax belongs to a top-level Figure (never a SubFigure) in this app.
        fig = ax.figure  # type: ignore[assignment]

    if mode == "voltage_time":
        s = voltage_time_series(df)
        ax.plot(s.x, s.y, lw=0.8)
        ax.set_xlabel("Time (h)")
        ax.set_ylabel("Voltage (V)")
        ax.set_title(title or f"{item_id} — V vs t")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        return fig

    if mode == "voltage_capacity":
        traces = voltage_capacity_series(df, cycles)
        if not traces:
            raise RuntimeError("No cycles to plot")
        cmap = plt.colormaps["viridis"]
        for t in traces:
            ax.plot(t.x, t.y, color=cmap(t.frac), lw=1.0, label=f"cycle {t.cycle_id}")
        ax.set_xlabel("Capacity (mAh)")
        ax.set_ylabel("Voltage (V)")
        ax.set_title(title or f"{item_id} — V vs Q")
        ax.grid(alpha=0.3)
        if len(traces) <= 12:
            ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        return fig

    if mode == "dqdv":
        if cycles is None:
            raise ValueError("mode='dqdv' on plot_cell requires cycles=...")
        cmap = plt.colormaps["viridis"]
        for t in dqdv_series(df, cycles):
            ax.plot(t.x, t.y, color=cmap(t.frac), lw=1.0, label=f"cycle {t.cycle_id}")
        ax.set_xlabel("Voltage (V)")
        ax.set_ylabel("dQ/dV (mA/V)")
        ax.set_title(title or f"{item_id} — dQ/dV")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        return fig

    raise ValueError(f"Unknown mode {mode!r}")
