"""Tests for datalab_plot.series — the render-agnostic plot-series layer."""
from __future__ import annotations

import numpy as np

from datalab_plot.series import (
    CycleTrace,
    SummarySeries,
    XYSeries,
    cumulative_time_hours,
    cycle_cmap,
    cycle_ids,
    dqdv_series,
    summary_series,
    voltage_capacity_series,
    voltage_time_series,
)


def test_cycle_ids(echem_df):
    assert cycle_ids(echem_df) == [1, 2, 3]
    # No 'full cycle' column -> empty, not an error.
    assert cycle_ids(echem_df.drop(columns=["full cycle"])) == []


def test_summary_series(echem_df):
    s = summary_series(echem_df)
    assert isinstance(s, SummarySeries)
    assert list(s.cycle) == [1, 2, 3]
    assert len(s.discharge_mah) == 3
    assert len(s.ce_percent) == 3
    assert np.all(np.isfinite(s.ce_percent))
    # No mass supplied → specific capacity is absent.
    assert s.discharge_mah_g is None
    # Values must be in mAh range (conftest uses ~95–100 mAh half-cycles).
    assert s.discharge_mah.max() > 10.0, "discharge_mah looks like Ah (units regression?)"
    assert s.discharge_mah.max() < 200.0, "discharge_mah looks like μAh (units regression?)"


def test_summary_series_specific_capacity(echem_df):
    mass_g = 0.010  # 10 mg
    s = summary_series(echem_df, mass_g=mass_g)
    assert s.discharge_mah_g is not None
    np.testing.assert_allclose(s.discharge_mah_g, s.discharge_mah / mass_g)


def test_summary_series_zero_mass_skips_specific(echem_df):
    s = summary_series(echem_df, mass_g=0.0)
    assert s.discharge_mah_g is None


def test_voltage_capacity_series_all_cycles(echem_df):
    traces = voltage_capacity_series(echem_df)
    assert len(traces) == 3
    assert all(isinstance(t, CycleTrace) for t in traces)
    assert [t.cycle_id for t in traces] == [1, 2, 3]
    # frac spans 0..1 across cycles.
    assert [t.frac for t in traces] == [0.0, 0.5, 1.0]
    # Each trace carries the half-cycle NaN separator.
    for t in traces:
        assert np.isnan(t.x).sum() == 1
        assert len(t.x) == len(t.y)


def test_voltage_capacity_series_restricted(echem_df):
    traces = voltage_capacity_series(echem_df, cycles=[1, 3])
    assert [t.cycle_id for t in traces] == [1, 3]


def test_dqdv_series_returns_cycle_traces(echem_df):
    traces = dqdv_series(echem_df, 1)
    assert isinstance(traces, list)
    # navani may yield no derivative for synthetic data; whatever it returns
    # must be well-formed CycleTraces.
    for t in traces:
        assert isinstance(t, CycleTrace)
        assert len(t.x) == len(t.y)


def test_dqdv_series_empty_for_missing_cycle(echem_df):
    assert dqdv_series(echem_df, 999) == []


def test_voltage_time_series(echem_df):
    s = voltage_time_series(echem_df)
    assert isinstance(s, XYSeries)
    assert len(s.x) == len(s.y) == len(echem_df)
    # Time is monotonic in the fixture -> elapsed hours are non-decreasing.
    assert np.all(np.diff(s.x) >= 0)


def test_cumulative_time_hours_uses_monotonic_column(echem_df):
    hours = cumulative_time_hours(echem_df)
    assert hours.iloc[0] == 0.0
    assert hours.is_monotonic_increasing


def test_cycle_cmap_first_is_orange():
    _, name = cycle_cmap(0)
    assert name == "oranges"


def test_cycle_cmap_picks_distinct_gradients():
    a, name_a = cycle_cmap(0)
    b, name_b = cycle_cmap(1)
    c, name_c = cycle_cmap(2)
    assert len({name_a, name_b, name_c}) == 3
    # Each gradient runs from a saturated start to a dark end (late = dark).
    for cmap in (a, b, c):
        start_lum = sum(cmap(0.0)[:3])
        end_lum = sum(cmap(1.0)[:3])
        assert end_lum < start_lum


def test_cycle_cmap_indices_wrap():
    _, name_a = cycle_cmap(0)
    _, name_b = cycle_cmap(6)  # 6 % 6 == 0, same family as cell 0
    assert name_a == name_b
