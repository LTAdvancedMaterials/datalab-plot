"""Smoke tests for the matplotlib echem builders.

These confirm the builders still render after being switched to consume
``datalab_plot.series`` (Phase 2.4). They call the private ``_plot_*``
helpers directly with synthetic data so no datalab client is needed.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402

from datalab_plot.plots.echem import (  # noqa: E402
    _assign_colors,
    _normalise_items,
    _plot_dqdv,
    _plot_summary,
    _plot_voltage_capacity,
    _plot_voltage_time,
)


def _items_colors_raw(echem_df):
    items = _normalise_items(["cell"])
    colors = _assign_colors(items)
    return items, colors, {"cell": echem_df}


def test_plot_summary_smoke(echem_df):
    items, colors, raw = _items_colors_raw(echem_df)
    fig = _plot_summary(items, raw, colors, title=None)
    assert isinstance(fig, Figure)
    assert len(fig.axes) >= 2  # two-panel: capacity + CE


def test_plot_voltage_capacity_smoke(echem_df):
    items, _colors, raw = _items_colors_raw(echem_df)
    fig = _plot_voltage_capacity(items, raw, ax=None, title=None)
    assert isinstance(fig, Figure)
    # 3 synthetic cycles -> 3 plotted lines.
    assert len(fig.axes[0].lines) == 3


def test_plot_voltage_time_smoke(echem_df):
    items, colors, raw = _items_colors_raw(echem_df)
    fig = _plot_voltage_time(items, raw, colors, ax=None, title=None)
    assert isinstance(fig, Figure)
    assert len(fig.axes[0].lines) == 1


def test_plot_dqdv_smoke(echem_df):
    items, colors, raw = _items_colors_raw(echem_df)
    # Whatever navani yields for the derivative, the builder must return a
    # Figure without raising.
    fig = _plot_dqdv(items, raw, colors, cycle=1, ax=None, title=None)
    assert isinstance(fig, Figure)
