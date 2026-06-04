"""Render-agnostic plot-series builders.

This module sits between the parsers (raw navani DataFrames) and the two
rendering backends — matplotlib in ``plots/`` and Plotly in ``gui/``. Each
builder turns a DataFrame into plain arrays / ``NamedTuple``s so both backends
draw *identical* data and the per-cycle iteration logic lives in one place.

Builders here are pure: no plotting, no I/O, no Streamlit.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd

from .parsers.echem import compute_dqdv, cycle_summary, filter_by_cycle, split_half_cycles

# Perceptually uniform sequential colormaps; one per cell in V-Q plots,
# cycled if more cells than colormaps are selected. Used by both backends.
PER_CELL_CMAPS = ("viridis", "plasma", "inferno", "magma", "cividis")


class SummarySeries(NamedTuple):
    """Per-cycle discharge capacity and Coulombic efficiency for one cell."""

    cycle: np.ndarray
    discharge_mah: np.ndarray
    ce_percent: np.ndarray


class CycleTrace(NamedTuple):
    """One cycle's ``(x, y)`` line data.

    ``frac`` is the cycle's position in ``0..1`` along its cell's colormap
    (early cycle = 0.0, last = 1.0), so renderers colour cycles consistently.
    """

    cycle_id: int
    x: np.ndarray
    y: np.ndarray
    frac: float


class XYSeries(NamedTuple):
    """A single ``(x, y)`` line."""

    x: np.ndarray
    y: np.ndarray


def cycle_ids(df: pd.DataFrame) -> list[int]:
    """Sorted positive ``full cycle`` numbers present in ``df`` (empty if none)."""
    if "full cycle" not in df.columns:
        return []
    return sorted({int(c) for c in df["full cycle"].dropna().unique() if c > 0})


def summary_series(df: pd.DataFrame) -> SummarySeries:
    """Discharge capacity (mAh) and Coulombic efficiency (%) vs cycle number."""
    summ = cycle_summary(df)
    return SummarySeries(
        cycle=summ["cycle"].to_numpy(),
        discharge_mah=summ["Discharge_mAh"].to_numpy(),
        ce_percent=(100.0 * summ["CE"]).to_numpy(),
    )


def voltage_capacity_series(
    df: pd.DataFrame, cycles: int | list[int] | None = None
) -> list[CycleTrace]:
    """V-Q line data, one :class:`CycleTrace` per full cycle.

    Rest rows (``state == 'R'``) are dropped: V-Q is a charge-transfer
    characteristic, and rest periods sit at constant Q while V relaxes —
    drawing them produces vertical "OCV recovery" lines that aren't part of
    the cycling curve. Mid-cycle pauses are dropped for the same reason.

    Each trace already carries NaN separators between half-cycles (via
    :func:`split_half_cycles`). Pass ``cycles`` to restrict; default is every
    cycle in ``df``.
    """
    filt = filter_by_cycle(df, cycles) if cycles is not None else df
    if "state" in filt.columns:
        filt = filt[filt["state"] != "R"]
    ids = cycle_ids(filt)
    n = len(ids)
    traces: list[CycleTrace] = []
    for j, cid in enumerate(ids):
        cyc = filt[filt["full cycle"] == cid]
        x, y = split_half_cycles(cyc, "Capacity", "Voltage")
        traces.append(CycleTrace(cid, x, y, j / max(1, n - 1)))
    return traces


def dqdv_series(df: pd.DataFrame, cycle: int | list[int] | None) -> list[CycleTrace]:
    """dQ/dV line data, one :class:`CycleTrace` per full cycle after differencing.

    Returns an empty list when ``cycle`` selects no data or navani yields no
    usable derivative (e.g. rests / segments too short).
    """
    cyc = filter_by_cycle(df, cycle)
    if cyc.empty:
        return []
    diff = compute_dqdv(cyc, mode="dQ/dV")
    if diff.empty:
        return []
    ids = sorted(int(c) for c in diff["full cycle"].unique())
    n = len(ids)
    traces: list[CycleTrace] = []
    for j, cid in enumerate(ids):
        seg = diff[diff["full cycle"] == cid]
        x, y = split_half_cycles(seg, "voltage (V)", "dQ/dV (mA/V)")
        traces.append(CycleTrace(cid, x, y, j / max(1, n - 1)))
    return traces


def cumulative_time_hours(df: pd.DataFrame) -> pd.Series:
    """Return a monotonic elapsed-time series in hours for a navani DataFrame.

    Preference order:
      1. ``Test_Time(s)`` — the cycler's running clock (Arbin etc.), monotonic
         by definition, never resets per step or cycle.
      2. ``Time`` — navani's standardised column, if it happens to be monotonic.
      3. Reconstruct from non-monotonic time by clamping negative deltas to 0
         (so step-time / cycle-time resets are absorbed into a forward-only sum).
    """
    for col in ("Test_Time(s)", "Time"):
        if col in df.columns and df[col].is_monotonic_increasing:
            return df[col] / 3600.0
    # Fallback: collapse resets. Use whatever time-like column exists.
    src = df["Time"] if "Time" in df.columns else df["Test_Time(s)"]
    deltas = src.diff().fillna(0).clip(lower=0)
    return deltas.cumsum() / 3600.0


def voltage_time_series(df: pd.DataFrame) -> XYSeries:
    """Voltage vs cumulative elapsed time (hours)."""
    t = cumulative_time_hours(df)
    return XYSeries(x=t.to_numpy(), y=df["Voltage"].to_numpy())
