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

    Each full cycle is a charge half-cycle (``state == 0``) followed by a
    discharge half-cycle (``state == 1``) — navani's convention, the same one
    ``_classify_neware_status`` maps ``*_Chg`` / ``*_DChg`` onto. Half-cycle
    numbering is also navani's: charge ``2c-1``, discharge ``2c``. ``Capacity`` is cumulative
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
                    "state": 0,
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
                    "state": 1,
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


# ── synthetic NewareNDA-style frames ─────────────────────────────────────────
#
# `_read_neware` is the seam: these build what `NewareNDA.read` returns, so the
# whole Neware path can be exercised without committing a real cycler binary.

_BASE_TS = pd.Timestamp("2026-07-18 18:00:00")


def _step_rows(cycle, step, step_index, status, start_s, duration_s, n,
               current_mA, volts, counter_col=None, counter_end=None):
    """Rows for one Neware step, mimicking ``NewareNDA.read`` output:

    per-step ``Time`` (resets to ~0 at each step), absolute ``Timestamp``, and
    per-step capacity counters (reset each step, ramping to ``counter_end``).
    """
    t = np.linspace(duration_s / n, duration_s, n)
    rows = []
    for i, ti in enumerate(t):
        chg = dchg = 0.0
        if counter_col == "chg":
            chg = counter_end * (i + 1) / n
        elif counter_col == "dchg":
            dchg = counter_end * (i + 1) / n
        rows.append({
            "Cycle": cycle,
            "Step": step,
            "Step_Index": step_index,
            "Status": status,
            "Time": ti,
            "Timestamp": _BASE_TS + pd.Timedelta(seconds=start_s + ti),
            "Voltage": volts,
            "Current(mA)": current_mA,
            "Charge_Capacity(mAh)": chg,
            "Discharge_Capacity(mAh)": dchg,
        })
    return rows


def make_neware_raw_df() -> pd.DataFrame:
    """A two-cycle CCCV formation at 1 mA where cycle 2 is *shorter* than
    cycle 1 (the SEI-consumed first charge is the longest), with ``Step_Index``
    restarting per cycle — the exact shape that breaks navani's time rebuild.

    True capacities: cycle 1 charge 1.0 + 0.05 CV = 1.05, discharge 0.80;
    cycle 2 charge 0.80 + 0.025 CV = 0.825, discharge 0.78.
    """
    rows: list[dict] = []
    t = 0.0

    def add(step, step_index, status, duration, n, current, volts, col=None, end=None):
        nonlocal t
        rows.extend(_step_rows(1 if step <= 6 else 2, step, step_index, status,
                               t, duration, n, current, volts, col, end))
        t += duration

    # cycle 1
    add(1, 1, "Rest", 100, 4, 0.0, 3.0)
    add(2, 2, "CC_Chg", 3600, 40, 1.0, 3.8, "chg", 1.0)
    add(3, 3, "CV_Chg", 180, 4, 0.5, 4.2, "chg", 0.05)
    add(4, 4, "Rest", 100, 4, 0.0, 4.1)
    add(5, 5, "CC_DChg", 2880, 30, -1.0, 3.6, "dchg", 0.8)
    add(6, 6, "Rest", 100, 4, 0.0, 3.1)
    # cycle 2 — Step_Index restarts at 2 (the protocol loops)
    add(7, 2, "CC_Chg", 2880, 30, 1.0, 3.9, "chg", 0.8)
    add(8, 3, "CV_Chg", 90, 4, 0.5, 4.2, "chg", 0.025)
    add(9, 4, "Rest", 100, 4, 0.0, 4.1)
    add(10, 5, "CC_DChg", 2808, 30, -1.0, 3.6, "dchg", 0.78)
    add(11, 6, "Rest", 100, 4, 0.0, 3.1)
    return pd.DataFrame(rows)


@pytest.fixture
def neware_raw_df() -> pd.DataFrame:
    return make_neware_raw_df()


@pytest.fixture
def fake_neware(monkeypatch) -> pd.DataFrame:
    """Point ``parsers.echem._read_neware`` at the synthetic formation frame."""
    from datalab_plot.parsers import echem as echem_parser

    frame = make_neware_raw_df()
    monkeypatch.setattr(echem_parser, "_read_neware", lambda path: frame.copy())
    return frame
