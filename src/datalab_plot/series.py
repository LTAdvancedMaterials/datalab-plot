"""Render-agnostic plot-series builders.

This module sits between the parsers (raw navani DataFrames) and the two
rendering backends — matplotlib in ``plots/`` and Plotly in ``gui/``. Each
builder turns a DataFrame into plain arrays / ``NamedTuple``s so both backends
draw *identical* data and the per-cycle iteration logic lives in one place.

Builders here are pure: no plotting, no I/O, no Streamlit.
"""
from __future__ import annotations

from typing import NamedTuple

import matplotlib.pyplot as _plt
import numpy as np
import pandas as pd
from matplotlib.colors import Colormap, LinearSegmentedColormap

from .parsers.echem import compute_dqdv, cycle_summary, filter_by_cycle, split_half_cycles

# V-Q cycle colouring is driven through `cycle_cmap` so both backends use the
# same gradients. Each cell gets a distinct colour-to-dark gradient drawn
# from matplotlib's single-hue sequential colormaps, clamped to [0.4, 1.0]
# so cycles start saturated rather than near-white. Late cycle = dark end.
# Orange is first because it's the most legible default on a white
# background and is visually distinct from the GUI's chrome.


def _clamped(base: str, lo: float = 0.4, hi: float = 1.0, n: int = 256) -> Colormap:
    """Return a 256-stop ``LinearSegmentedColormap`` of ``base`` over ``[lo, hi]``.

    Matches the ``0.4 + 0.55·…`` clamping pattern used for cell-grouping
    colours in ``plots/echem.py`` (``_assign_colors``), so V-Q per-cell
    gradients stay visually consistent with the rest of the GUI.
    """
    src = _plt.colormaps[base]
    return LinearSegmentedColormap.from_list(
        base.lower(), [src(lo + (hi - lo) * i / (n - 1)) for i in range(n)]
    )


_PER_CELL_CMAPS = (
    _clamped("Oranges"),
    _clamped("Blues"),
    _clamped("Greens"),
    _clamped("Purples"),
    _clamped("Reds"),
    _clamped("Greys"),
)


def cycle_cmap(cell_idx: int) -> tuple[Colormap, str]:
    """Pick the per-cell cmap for a V-Q trace.

    Each cell gets a distinct colour-to-dark gradient indexed by
    ``cell_idx`` (cycled with ``%`` if there are more cells than
    gradients). Returns ``(cmap, short display name)`` so callers can
    label the legend.
    """
    cmap = _PER_CELL_CMAPS[cell_idx % len(_PER_CELL_CMAPS)]
    return cmap, cmap.name


class SummarySeries(NamedTuple):
    """Per-cycle charge/discharge capacity and Coulombic efficiency for one cell."""

    cycle: np.ndarray
    discharge_mah: np.ndarray
    charge_mah: np.ndarray
    ce_percent: np.ndarray
    discharge_mah_g: np.ndarray | None = None
    charge_mah_g: np.ndarray | None = None


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


def summary_series(df: pd.DataFrame, mass_g: float | None = None) -> SummarySeries:
    """Charge/discharge capacity (mAh) and Coulombic efficiency (%) vs cycle number.

    If ``mass_g`` is provided (cathode active mass in grams), also populates
    ``discharge_mah_g`` and ``charge_mah_g`` (specific capacities in mAh/g).
    """
    summ = cycle_summary(df)
    discharge_mah = summ["Discharge_mAh"].to_numpy()
    charge_mah = summ["Charge_mAh"].to_numpy()
    has_mass = mass_g is not None and mass_g > 0
    return SummarySeries(
        cycle=summ["cycle"].to_numpy(),
        discharge_mah=discharge_mah,
        charge_mah=charge_mah,
        ce_percent=(100.0 * summ["CE"]).to_numpy(),
        discharge_mah_g=discharge_mah / mass_g if has_mass else None,
        charge_mah_g=charge_mah / mass_g if has_mass else None,
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
