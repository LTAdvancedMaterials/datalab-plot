"""Tests for datalab_plot.parsers.echem — the pure data-transform layer."""
from __future__ import annotations

import numpy as np
import pandas as pd

from datalab_plot.parsers.echem import (
    compute_dqdv,
    cycle_summary,
    detect_status_column,
    filter_by_cycle,
    is_cycling_file,
    split_by_status,
    split_half_cycles,
)


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
