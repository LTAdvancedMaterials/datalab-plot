"""Tests for datalab_plot.parsers.echem — the pure data-transform layer."""
from __future__ import annotations

import numpy as np
import pandas as pd

from datalab_plot.parsers.echem import (
    _normalise_neware_state,
    compute_dqdv,
    cycle_summary,
    detect_status_column,
    filter_by_cycle,
    is_cycling_file,
    split_by_status,
    split_half_cycles,
)
from datalab_plot.series import voltage_capacity_series


def test_is_cycling_file():
    assert is_cycling_file({"name": "run.mpr"})
    assert is_cycling_file({"name": "DATA.NDAX"})  # case-insensitive
    assert not is_cycling_file({"name": "image.png"})
    assert not is_cycling_file({"name": ""})
    assert not is_cycling_file({})


def test_filter_by_cycle_selects_full_cycles(echem_df):
    one = filter_by_cycle(echem_df, 2)
    assert set(one["full cycle"].unique()) == {2}

    multi = filter_by_cycle(echem_df, [1, 3])
    assert set(multi["full cycle"].unique()) == {1, 3}

    # None is a pass-through.
    assert filter_by_cycle(echem_df, None) is echem_df


def test_filter_by_cycle_ignores_nonpositive(echem_df):
    # 0 / negatives are dropped; only valid cycle 1 survives.
    assert set(filter_by_cycle(echem_df, [0, 1, -2])["full cycle"].unique()) == {1}


def test_split_half_cycles_inserts_one_nan_gap(echem_df):
    cycle1 = filter_by_cycle(echem_df, 1)  # one charge + one discharge half
    x, y = split_half_cycles(cycle1, "Capacity", "Voltage")
    assert np.isnan(x).sum() == 1
    assert np.isnan(y).sum() == 1
    # All real points preserved plus the single separator.
    assert len(x) == len(cycle1) + 1


def test_split_half_cycles_empty():
    x, y = split_half_cycles(pd.DataFrame(), "Capacity", "Voltage")
    assert len(x) == 0 and len(y) == 0


def test_detect_status_column(echem_df):
    assert detect_status_column(echem_df) == "Status"
    # A frame with no varying status-like column -> None.
    flat = pd.DataFrame({"Voltage": [1.0, 2.0], "state": [1, 1]})
    assert detect_status_column(flat) is None


def test_split_by_status_runs(echem_df):
    runs = split_by_status(echem_df, "Capacity", "Voltage", "Status")
    # 3 cycles x (charge, discharge) = 6 contiguous status runs.
    assert len(runs) == 6
    statuses = [s for _, _, s in runs]
    assert statuses == ["CC_Chg", "CC_DChg"] * 3
    # First run starts clean; later runs carry the previous run's last point.
    first_x, _, _ = runs[0]
    second_x, _, _ = runs[1]
    assert len(second_x) == len(first_x) + 1


def test_split_by_status_missing_column(echem_df):
    assert split_by_status(echem_df, "Capacity", "Voltage", "nope") == []


def test_cycle_summary(echem_df):
    summ = cycle_summary(echem_df)
    assert {"cycle", "Charge_mAh", "Discharge_mAh", "CE"}.issubset(summ.columns)
    assert len(summ) == 3
    assert list(summ["cycle"]) == [1, 2, 3]
    ce = summ["CE"].to_numpy()
    # Every cycle is identical synthetic data, so CE is constant and finite.
    # (Don't pin the exact value — navani decides which half is charge vs
    # discharge, and the 100/95 mAh ratio may land either way up.)
    assert np.all(np.isfinite(ce))
    np.testing.assert_allclose(ce, ce[0], rtol=1e-6)
    assert ce[0] > 0


def test_compute_dqdv_columns(echem_df):
    result = compute_dqdv(echem_df, window_size_1=21, window_size_2=51)
    assert isinstance(result, pd.DataFrame)
    # Whether or not navani yields points, the schema is fixed.
    for col in ("voltage (V)", "capacity (mAh)", "dQ/dV (mA/V)", "full cycle", "half cycle"):
        assert col in result.columns


def test_compute_dqdv_too_short_returns_empty():
    tiny = pd.DataFrame({"Voltage": [3.0], "Capacity": [0.0], "half cycle": [1], "full cycle": [1]})
    assert compute_dqdv(tiny).empty


# --- Neware Status normalisation -------------------------------------------

def _neware_step(status: str, current_ma: float, n: int, v_start: float, v_end: float):
    """One Neware step: ``Status``, ``Current``, ``Voltage`` with ``n`` rows."""
    return pd.DataFrame({
        "Status": [status] * n,
        "Current": np.full(n, current_ma, dtype=float),
        "Voltage": np.linspace(v_start, v_end, n),
    })


def _make_neware_cccv_df(n_cycles: int = 2) -> pd.DataFrame:
    """Two-cycle synthetic Neware-shaped frame: CC_Chg → CV_Chg → Rest →
    CC_DChg → Rest. Mirrors what ``neware_reader_nda`` returns *except* that
    ``state`` is left as ``"unknown"`` for every CV row (which is the upstream
    bug ``_normalise_neware_state`` exists to repair).
    """
    parts: list[pd.DataFrame] = []
    for _ in range(n_cycles):
        parts.append(_neware_step("CC_Chg",  +1.0, 60, 3.0, 4.2))
        parts.append(_neware_step("CV_Chg",  +0.2, 30, 4.2, 4.2))
        parts.append(_neware_step("Rest",     0.0, 10, 4.2, 4.1))
        parts.append(_neware_step("CC_DChg", -1.0, 60, 4.2, 3.0))
        parts.append(_neware_step("Rest",     0.0, 10, 3.0, 3.1))
    df = pd.concat(parts, ignore_index=True)
    df["Time"] = np.arange(len(df), dtype=float) * 30.0  # 30 s per row
    # Mimic navani's broken pre-fix categorical: only CC_Chg / CC_DChg / Rest
    # are recognised; every CV row reads as the literal "unknown" string.
    state = pd.Series(["unknown"] * len(df), dtype=object)
    state[df["Status"] == "Rest"] = "R"
    state[df["Status"] == "CC_Chg"] = 0
    state[df["Status"] == "CC_DChg"] = 1
    df["state"] = state
    return df


def test_normalise_neware_state_is_noop_without_status_column(echem_df):
    df = echem_df.drop(columns=["Status"])
    out = _normalise_neware_state(df)
    # No Status -> the frame is returned untouched (same object, no copy).
    assert out is df


def test_normalise_neware_state_is_noop_for_non_neware_status(echem_df):
    # echem_df has Status values "CC_Chg" / "CC_DChg" which *are* Neware
    # canonical names, so the normaliser will fire. Swap in a non-Neware
    # vocabulary to confirm the early-out path leaves the frame alone.
    df = echem_df.copy()
    df["Status"] = "Galvanostatic"  # not in the Neware status set
    out = _normalise_neware_state(df)
    pd.testing.assert_frame_equal(out, df)


def test_normalise_neware_state_collapses_cv_into_parent_half_cycle():
    df = _make_neware_cccv_df(n_cycles=2)
    # Pre-fix: navani would assign each CC↔CV transition its own half cycle,
    # so 2 real cycles inflate to 4 half cycles per cycle (= 8 total) and the
    # discharge of cycle 1 lands on full_cycle 2 instead of 1.
    assert (df["state"] == "unknown").any()

    out = _normalise_neware_state(df)

    # Every row has a real state — no leftover "unknown" sentinels.
    assert set(out["state"].unique()) <= {0, 1, "R"}
    # CV rows now belong to the surrounding charge/discharge state.
    assert (out.loc[df["Status"] == "CV_Chg", "state"] == 0).all()
    # Two real cycles, four half cycles (one per CC step), no inflation.
    assert out["full cycle"].max() == 2
    assert out["half cycle"].max() == 4


def test_voltage_capacity_series_continuous_across_cv_step():
    df = _normalise_neware_state(_make_neware_cccv_df(n_cycles=2))
    traces = voltage_capacity_series(df)

    # One CycleTrace per real cycle — not three or more.
    assert [t.cycle_id for t in traces] == [1, 2]

    # Within each trace, the half-cycle segments separated by NaN must each
    # be monotonically non-decreasing in Capacity. A backward jump would
    # mean Neware's per-step Capacity reset survived the rebuild — which is
    # what produces the "diagonal connectors" and the disconnected CV
    # fragment seen on CEL-085.
    for t in traces:
        x = t.x
        gaps = np.flatnonzero(np.isnan(x))
        # Exactly one NaN separator: charge half | discharge half.
        assert len(gaps) == 1
        for seg in (x[: gaps[0]], x[gaps[0] + 1 :]):
            seg = seg[~np.isnan(seg)]
            assert seg[0] == 0.0  # Capacity resets to 0 at half-cycle start
            assert np.all(np.diff(seg) >= -1e-9)  # monotonic within the half


def test_normalise_neware_state_handles_protocol_markers():
    # A "Cycle" / "Pulse" / "Control" row at the head of the file (Neware
    # programs often emit these before any real cycling) must not steal a
    # half cycle from the first CC_Chg.
    df = pd.DataFrame({
        "Status": ["Cycle", "Pulse", "CC_Chg", "CC_Chg", "CC_DChg", "CC_DChg"],
        "Current": [0.0, 0.0, 1.0, 1.0, -1.0, -1.0],
        "Voltage": [3.0, 3.0, 3.0, 4.2, 4.2, 3.0],
        "Time": [0.0, 30.0, 60.0, 90.0, 120.0, 150.0],
    })
    out = _normalise_neware_state(df)
    # Protocol markers are treated as rest, so they hold half_cycle 0 and
    # don't push the first real charge to full_cycle 2.
    assert out.loc[:1, "half cycle"].tolist() == [0, 0]
    assert out.loc[2:, "full cycle"].tolist() == [1, 1, 1, 1]
