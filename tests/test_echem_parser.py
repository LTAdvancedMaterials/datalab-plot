"""Tests for datalab_plot.parsers.echem — the pure data-transform layer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import CHARGE_CAP_MAH, DISCHARGE_CAP_MAH, make_echem_df

from datalab_plot.parsers import echem as echem_parser
from datalab_plot.parsers.echem import (
    _accumulate_resets,
    _continuous_time_s,
    _normalise_neware_state,
    _stitch,
    compute_dqdv,
    cycle_summary,
    detect_status_column,
    filter_by_cycle,
    is_cycling_file,
    load_echem,
    split_by_status,
    split_half_cycles,
    stitch_frames,
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
    assert {"cycle", "Charge_mAh", "Discharge_mAh", "Charge_Ah", "Discharge_Ah", "CE"}.issubset(
        summ.columns
    )
    assert len(summ) == 3
    assert list(summ["cycle"]) == [1, 2, 3]

    # Charge and discharge must land on the right side of the ledger:
    # navani's convention is state 0 = charge, state 1 = discharge, and the
    # fixture's halves are 100 mAh charge / 95 mAh discharge.
    assert list(summ["Charge_mAh"]) == [CHARGE_CAP_MAH] * 3
    assert list(summ["Discharge_mAh"]) == [DISCHARGE_CAP_MAH] * 3
    np.testing.assert_allclose(summ["CE"], 0.95)

    # Capacity values must be in mAh (not Ah) — the navani path was
    # previously multiplying mAh values by 1000, giving values 1000× too large.
    all_cap_mah = np.concatenate([summ["Charge_mAh"].to_numpy(), summ["Discharge_mAh"].to_numpy()])
    assert all_cap_mah.max() > 10.0, (
        f"max capacity={all_cap_mah.max():.4f} — looks like Ah not mAh (units regression?)"
    )
    assert all_cap_mah.max() < 200.0, (
        f"max capacity={all_cap_mah.max():.1f} — looks like μAh not mAh (units regression?)"
    )

    np.testing.assert_allclose(summ["Charge_Ah"], summ["Charge_mAh"] / 1000.0)
    np.testing.assert_allclose(summ["Discharge_Ah"], summ["Discharge_mAh"] / 1000.0)

    ce = summ["CE"].to_numpy()
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


def test_normalise_neware_state_preserves_non_neware_rows_in_mixed_load():
    # multi_echem_file_loader stitches a Neware .ndax onto a Biologic .mpr:
    # the Neware rows carry a Status string, the Biologic rows have Status NaN
    # but a valid navani state (0/1/"R"). The normaliser must reclassify only
    # the Neware rows and leave the Biologic cycles intact — the regression
    # where every NaN-Status row collapsed to "R" and vanished from the
    # cycle-summary / V-Q plots while still showing in V-vs-t.
    neware = _make_neware_cccv_df(n_cycles=2)  # 2 Neware cycles, has Status

    # A Biologic-style tail: 2 more cycles, no Status column values, state set
    # by navani's bio_state (0 charge / 1 discharge / "R" rest).
    bio_parts: list[pd.DataFrame] = []
    for _ in range(2):
        bio_parts.append(pd.DataFrame({
            "Current": np.full(60, 1.0), "Voltage": np.linspace(3.0, 4.2, 60),
            "state": [0] * 60, "Capacity": np.linspace(0.0, 0.5, 60),
        }))
        bio_parts.append(pd.DataFrame({
            "Current": np.full(60, -1.0), "Voltage": np.linspace(4.2, 3.0, 60),
            "state": [1] * 60, "Capacity": np.linspace(0.0, 0.5, 60),
        }))
    bio = pd.concat(bio_parts, ignore_index=True)
    bio["Status"] = np.nan  # NaN for non-Neware rows, as after the concat

    combined = pd.concat([neware, bio], ignore_index=True)
    combined["Time"] = np.arange(len(combined), dtype=float) * 30.0

    out = _normalise_neware_state(combined)

    # Biologic rows keep their navani state — they are NOT forced to "R".
    bio_mask = combined["Status"].isna()
    assert set(out.loc[bio_mask, "state"].unique()) == {0, 1}
    # All four cycles survive (2 Neware + 2 Biologic), not just the Neware two.
    assert out["full cycle"].max() == 4
    # The summary spans every cycle with non-zero capacity on the Biologic tail.
    summ = cycle_summary(out)
    assert summ["cycle"].max() == 4
    assert (summ["Discharge_mAh"] > 0).all()


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


# ── building blocks ──────────────────────────────────────────────────────────


def test_accumulate_resets():
    out = _accumulate_resets([0.0, 1.0, 2.0, 0.0, 0.5, 0.0, 0.1])
    assert out.tolist() == [0.0, 1.0, 2.0, 2.0, 2.5, 2.5, 2.6]


def test_accumulate_resets_holds_through_nan():
    out = _accumulate_resets([0.0, 1.0, np.nan, 1.5, 0.0, 0.2])
    assert out.tolist() == [0.0, 1.0, 1.0, 1.5, 1.5, 1.7]


def test_accumulate_resets_empty():
    assert _accumulate_resets([]).tolist() == []


def test_continuous_time_prefers_timestamp():
    df = pd.DataFrame({
        # Relative time collapses cycles onto each other (the navani bug shape);
        # the absolute Timestamp must win.
        "Time": [0.0, 10.0, 0.0, 10.0],
        "Timestamp": pd.to_datetime([
            "2026-01-01 00:00:00", "2026-01-01 00:00:10",
            "2026-01-01 00:00:20", "2026-01-01 00:00:30",
        ]),
    })
    sec = _continuous_time_s(df)
    assert sec is not None
    assert sec.tolist() == [0.0, 10.0, 20.0, 30.0]


def test_continuous_time_fallback_clamps_resets():
    df = pd.DataFrame({"Time": [0.0, 10.0, 20.0, 0.0, 10.0]})
    sec = _continuous_time_s(df)
    assert sec is not None
    assert sec.tolist() == [0.0, 10.0, 20.0, 20.0, 30.0]


def test_continuous_time_rejects_non_monotonic_timestamp():
    # A Timestamp column that goes backwards (clock change, bad export) must
    # not be trusted; the relative fallback takes over.
    df = pd.DataFrame({
        "Time": [0.0, 10.0, 20.0],
        "Timestamp": pd.to_datetime([
            "2026-01-01 01:00:00", "2026-01-01 00:00:10", "2026-01-01 00:00:20",
        ]),
    })
    sec = _continuous_time_s(df)
    assert sec is not None
    assert sec.tolist() == [0.0, 10.0, 20.0]


def test_continuous_time_returns_none_without_a_time_source():
    assert _continuous_time_s(pd.DataFrame({"Voltage": [3.0, 3.1]})) is None


def test_cycle_summary_state_convention():
    # charge = state 0, discharge = state 1 (navani's convention) — guards
    # against the swapped mapping the old fallback aggregation had.
    df = pd.DataFrame({
        "full cycle": [1, 1, 1, 1],
        "state": [0, 0, 1, 1],
        "Capacity": [0.5, 1.0, 0.4, 0.8],
    })
    summ = cycle_summary(df)
    row = summ.iloc[0]
    assert row["Charge_mAh"] == 1.0
    assert row["Discharge_mAh"] == 0.8
    assert row["CE"] == pytest.approx(0.8)


def test_cycle_summary_does_not_mutate_its_input():
    # navani.echem.cycle_summary writes `full cycle` back onto the caller's
    # frame, flipping it int -> float. The GUI caches that frame, so the
    # replacement must be pure.
    df = make_echem_df(2)
    before = df["full cycle"].dtype
    cycle_summary(df)
    assert df["full cycle"].dtype == before


def test_cycle_summary_falls_back_to_half_cycle():
    df = pd.DataFrame({
        "half cycle": [1, 1, 2, 2],
        "state": [0, 0, 1, 1],
        "Capacity": [0.5, 1.0, 0.4, 0.8],
    })
    summ = cycle_summary(df)
    assert list(summ["cycle"]) == [1]
    assert summ.iloc[0]["Charge_mAh"] == 1.0


def test_cycle_summary_raises_without_cycle_columns():
    with pytest.raises(ValueError, match="neither"):
        cycle_summary(pd.DataFrame({"Capacity": [1.0], "state": [0]}))


# ── the regression: repeated Step_Index must not collapse cycle 2's timeline ──


def test_neware_cycle2_capacity_not_inflated(fake_neware):
    summ = cycle_summary(load_echem("formation.ndax"))
    by_cycle = {int(r["cycle"]): r for _, r in summ.iterrows()}

    assert by_cycle[1]["Charge_mAh"] == pytest.approx(1.05, rel=1e-6)
    assert by_cycle[1]["Discharge_mAh"] == pytest.approx(0.80, rel=1e-6)
    # navani's axis put cycle 2's charge on cycle 1's longer window; the true
    # value is 0.825.
    assert by_cycle[2]["Charge_mAh"] == pytest.approx(0.825, rel=1e-6)
    assert by_cycle[2]["Discharge_mAh"] == pytest.approx(0.78, rel=1e-6)
    assert by_cycle[2]["CE"] == pytest.approx(0.78 / 0.825, rel=1e-6)


def test_neware_time_axis_is_continuous(fake_neware):
    df = load_echem("formation.ndax")
    t = df["Time"].to_numpy()
    assert np.all(np.diff(t) >= 0)
    # Cycle 2's rows sit *after* cycle 1's on the axis, not on top of them.
    c1_end = df.loc[df["Cycle"] == 1, "Time"].max()
    c2_start = df.loc[df["Cycle"] == 2, "Time"].min()
    assert c2_start >= c1_end
    # Raw per-step time is preserved under navani's name for it.
    assert "Step Time / s" in df.columns


def test_neware_cv_stays_inside_charge_half(fake_neware):
    # CC_Chg → CV_Chg is one half cycle (navani's reader split them, spawning
    # spurious halves); each cycle = exactly one charge + one discharge half.
    df = load_echem("formation.ndax")
    active = df[df["state"] != "R"]
    assert sorted(active["half cycle"].unique()) == [1, 2, 3, 4]
    # Capacity continues through the CC→CV boundary instead of resetting.
    cv1 = df[(df["Status"] == "CV_Chg") & (df["Cycle"] == 1)]
    assert cv1["Capacity"].min() > 1.0  # continues from the CC step's 1.0 mAh


def test_neware_falls_back_to_current_integration(monkeypatch, fake_neware):
    # Without the cycler's counter columns the capacity comes from ∫|I|·dt on
    # the (fixed) continuous time axis. The integral pins each half's first
    # active sample to zero, so on this coarsely-sampled frame (~96 s/record)
    # it undershoots by roughly one sample interval — the assertion is that
    # cycle 2 sits near its true 0.825, not inflated to cycle 1's 1.05.
    frame = fake_neware.drop(columns=["Charge_Capacity(mAh)", "Discharge_Capacity(mAh)"])
    monkeypatch.setattr(echem_parser, "_read_neware", lambda path: frame.copy())
    summ = cycle_summary(load_echem("formation.ndax"))
    by_cycle = {int(r["cycle"]): r for _, r in summ.iterrows()}
    assert by_cycle[2]["Charge_mAh"] == pytest.approx(0.825, rel=0.06)
    assert by_cycle[2]["Discharge_mAh"] == pytest.approx(0.78, rel=0.06)
    assert by_cycle[2]["Charge_mAh"] < 0.9 * by_cycle[1]["Charge_mAh"]


def test_neware_capacity_is_in_mah(fake_neware):
    # Guard the unit assumption the counter path newly depends on: some Neware
    # machines write Ah into a column named (mAh). A 1000x error would show up
    # here first.
    df = load_echem("formation.ndax")
    assert 0.001 < df["Capacity"].max() < 100.0


def test_neware_units_mismatch_is_logged(monkeypatch, fake_neware, caplog):
    # Counters 1000x too small relative to the current integral must warn.
    frame = fake_neware.copy()
    for col in ("Charge_Capacity(mAh)", "Discharge_Capacity(mAh)"):
        frame[col] = frame[col] / 1000.0
    monkeypatch.setattr(echem_parser, "_read_neware", lambda path: frame.copy())
    with caplog.at_level("WARNING"):
        load_echem("formation.ndax")
    assert any("capacity units" in r.message for r in caplog.records)


def test_neware_requires_a_status_column(monkeypatch, fake_neware):
    frame = fake_neware.drop(columns=["Status"])
    monkeypatch.setattr(echem_parser, "_read_neware", lambda path: frame.copy())
    with pytest.raises(ValueError, match="no Status column"):
        load_echem("formation.ndax")


def test_neware_feeds_voltage_capacity_series(fake_neware):
    # The downstream contract: two full cycles, each split into charge and
    # discharge halves with a NaN separator, capacity monotonic within a half.
    traces = voltage_capacity_series(load_echem("formation.ndax"), None)
    assert [t.cycle_id for t in traces] == [1, 2]
    for tr in traces:
        q = np.asarray(tr.x, dtype=float)
        assert np.isnan(q).sum() == 1, "one gap between the charge and discharge halves"
        for seg in np.split(q, np.where(np.isnan(q))[0]):
            seg = seg[~np.isnan(seg)]
            assert np.all(np.diff(seg) >= -1e-9), "capacity must not go backwards"


# ── multi-file stitching ─────────────────────────────────────────────────────


def _mini_frame(t0, state_seq, capacity_seq, current=1.0):
    """One file's worth of already-parsed rows, starting at absolute time t0."""
    n = len(state_seq)
    return pd.DataFrame({
        "Time": np.arange(n, dtype=float) * 60.0 + t0,
        "Voltage": np.linspace(3.0, 4.2, n),
        "Current": np.full(n, current, dtype=float),
        "state": list(state_seq),
        "Capacity": np.asarray(capacity_seq, dtype=float),
    })


def test_stitch_makes_time_continuous():
    a = _mini_frame(0.0, [0, 0, 0], [0.0, 1.0, 2.0])
    b = _mini_frame(9999.0, [1, 1, 1], [0.0, 1.0, 2.0])
    out = _stitch([a, b])
    t = out["Time"].to_numpy()
    assert np.all(np.diff(t) >= 0), "the second file must not jump backwards"
    assert t[0] == 0.0
    # b's absolute start is discarded; it resumes where a ended.
    assert t[3] == t[2]


def test_stitch_merges_a_half_cycle_spanning_the_boundary():
    # Both files are charge (state 0), so they are ONE half cycle, and the
    # second file's capacity must continue rather than restart at 0.
    a = _mini_frame(0.0, [0, 0, 0], [0.0, 1.0, 2.0])
    b = _mini_frame(0.0, [0, 0, 0], [0.0, 1.0, 2.0])
    out = _stitch([a, b])
    assert sorted(out["half cycle"].unique()) == [1]
    assert out["Capacity"].tolist() == [0.0, 1.0, 2.0, 2.0, 3.0, 4.0]


def test_stitch_keeps_distinct_half_cycles_apart():
    a = _mini_frame(0.0, [0, 0], [0.0, 1.0])
    b = _mini_frame(0.0, [1, 1], [0.0, 1.0], current=-1.0)
    out = _stitch([a, b])
    assert sorted(out["half cycle"].unique()) == [1, 2]


def test_stitch_frames_passthrough_and_guard():
    a = _mini_frame(0.0, [0, 0], [0.0, 1.0])
    assert stitch_frames([a]) is a
    with pytest.raises(ValueError, match="no frames"):
        stitch_frames([])


def test_load_echem_single_element_list_matches_bare_path(fake_neware):
    one = load_echem("formation.ndax")
    listed = load_echem(["formation.ndax"])
    pd.testing.assert_frame_equal(one, listed)


def test_load_echem_stitches_two_neware_files(fake_neware):
    # Two copies of the same formation run back to back: 4 full cycles, a
    # monotonic time axis, and no capacity discontinuity at the seam.
    df = load_echem(["a.ndax", "b.ndax"])
    assert np.all(np.diff(df["Time"].to_numpy()) >= 0)
    active = df[df["state"] != "R"]
    assert sorted(active["half cycle"].unique()) == [1, 2, 3, 4, 5, 6, 7, 8]
    summ = cycle_summary(df)
    assert list(summ["cycle"]) == [0, 1, 2, 3, 4]
