"""Shared pytest fixtures.

Per the maintainability plan, tests use **synthetic in-memory data only** — no
real company data files are committed. ``make_echem_df`` builds a DataFrame
whose columns match what ``navani`` produces and what
``datalab_plot.parsers.echem`` consumes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

CHARGE_CAP_MAH = 100.0
DISCHARGE_CAP_MAH = 95.0  # -> Coulombic efficiency == 0.95


def make_echem_df(n_cycles: int = 3, points_per_half: int = 300) -> pd.DataFrame:
    """Build a navani-style parsed cycler DataFrame.

    Each full cycle is a charge half-cycle (``state == 1``) followed by a
    discharge half-cycle (``state == 0``). Half-cycle numbering is navani's
    convention: charge ``2c-1``, discharge ``2c``. ``Capacity`` is cumulative
    within a half-cycle and resets at the boundary, so ``split_half_cycles``
    has a real reset to break on. A globally monotonic ``Time`` column (in
    seconds) is added so ``cumulative_time_hours`` has a clean source.
    """
    frames: list[pd.DataFrame] = []
    for cyc in range(1, n_cycles + 1):
        frames.append(
            pd.DataFrame(
                {
                    "Voltage": np.linspace(3.0, 4.2, points_per_half),
                    "Capacity": np.linspace(0.0, CHARGE_CAP_MAH, points_per_half),
                    "Current": np.full(points_per_half, 1.0),
                    "full cycle": cyc,
                    "half cycle": 2 * cyc - 1,
                    "state": 1,
                    "Status": "CC_Chg",
                }
            )
        )
        frames.append(
            pd.DataFrame(
                {
                    "Voltage": np.linspace(4.2, 3.0, points_per_half),
                    "Capacity": np.linspace(0.0, DISCHARGE_CAP_MAH, points_per_half),
                    "Current": np.full(points_per_half, -1.0),
                    "full cycle": cyc,
                    "half cycle": 2 * cyc,
                    "state": 0,
                    "Status": "CC_DChg",
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    df["Time"] = np.arange(len(df), dtype=float) * 30.0  # seconds, monotonic
    return df


@pytest.fixture
def echem_df() -> pd.DataFrame:
    """A 3-cycle synthetic cycler DataFrame."""
    return make_echem_df()
