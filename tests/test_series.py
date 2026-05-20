"""Tests for datalab_plot.series — the render-agnostic plot-series layer."""
from __future__ import annotations

import numpy as np

from datalab_plot.series import (
    CycleTrace,
    SummarySeries,
    XYSeries,
    cumulative_time_hours,
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
