"""Parse electrochemical cycling files and compute derived quantities.

Most formats go through ``navani.echem``. Neware ``.nda`` / ``.ndax`` do not:
navani's Neware reader groups on ``Step_Index``, the protocol step
definition number, which repeats every loop and corrupts the time axis at
every step boundary. Reported upstream as
https://github.com/be-smith/navani/issues/62. See ``_load_neware``
and the Neware section of ``CLAUDE.md`` for the measured error.

Backported from a private internal Lightning Tree application. See
``SYNC.md`` for the divergences that are deliberate, so a cleanup does
not undo a fix that exists only here.
"""
from __future__ import annotations

import logging
from pathlib import Path

import navani.echem as ec
import numpy as np
import pandas as pd


# Suppress the per-file INFO chatter from NewareNDA (.nda/.ndax). `_read_neware`
# passes log_level='WARNING' on every call, but NewareNDA resets its logger level
# from its own default on each read and any rich/pydatalab-style logging config
# can later flip it back — so a Filter on the 'newarenda' logger drops anything
# below WARNING regardless of the logger's nominal level. Filters survive
# setLevel(), so this catches resets we couldn't anticipate.
class _DropInfoFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return record.levelno >= logging.WARNING


logger = logging.getLogger(__name__)

_newarenda_logger = logging.getLogger("newarenda")
_newarenda_logger.setLevel(logging.WARNING)
if not any(isinstance(f, _DropInfoFilter) for f in _newarenda_logger.filters):
    _newarenda_logger.addFilter(_DropInfoFilter())

CYCLING_EXTENSIONS = (
    ".mpr",
    ".res",
    ".xls",
    ".xlsx",
    ".nda",
    ".ndax",
    ".csv",
    ".txt",
)

# Read directly by `_load_neware`, bypassing navani — see its docstring.
_NEWARE_EXTENSIONS = (".nda", ".ndax")


def is_cycling_file(file_meta: dict) -> bool:
    """Return True if a file dict from datalab looks like a cycler export."""
    name = (file_meta.get("name") or "").lower()
    if not name:
        return False
    for ext in CYCLING_EXTENSIONS:
        if name.endswith(ext):
            return True
    return False


def load_echem(paths: Path | list[Path]) -> pd.DataFrame:
    """Load one or more cycler files and return the raw DataFrame.

    Multiple files are parsed independently and stitched into one continuous
    run in list order — the caller orders them, which is upload order for a
    cell's files. ``navani.echem.multi_echem_file_loader`` is deliberately not
    used: it re-reads each file through navani's own loaders, so a Neware stack
    would inherit the broken time axis ``_load_neware`` exists to avoid, and it
    then re-integrates capacity against that same axis.
    """
    if isinstance(paths, (str, Path)):
        return _load_one(paths)
    paths = list(paths)
    if len(paths) == 1:
        return _load_one(paths[0])
    return _stitch([_load_one(p) for p in paths])


# The full set of strings ``NewareNDA`` writes into the ``Status`` column
# (NewareNDA/dicts.py:state_dict). ``navani.neware.neware_reader_nda`` only
# handles ``Rest``, ``CC_Chg`` and ``CC_DChg``; every other status (notably
# the CV phase that follows every CC charge in a CCCV protocol) is left as
# the categorical literal ``"unknown"``, which then compares as a distinct
# state in navani's half-cycle diff and produces a spurious half cycle at
# every CC↔CV step boundary.
_NEWARE_STATUSES = frozenset({
    "Rest", "Pause", "OCV",
    "CC_Chg", "CV_Chg", "CCCV_Chg", "CP_Chg", "CPCV_Chg",
    "CC_DChg", "CV_DChg", "CCCV_DChg", "CP_DChg", "CR_DChg", "CPCV_DChg",
    "Cycle", "Pulse", "SIM", "Control",
})


def _classify_neware_status(status: object) -> object:
    """Map a Neware ``Status`` string to navani's ``state`` convention.

    Charge variants (``CC_Chg``, ``CV_Chg``, ``CCCV_Chg``, …) collapse to
    ``0``; discharge variants (``CC_DChg``, ``CV_DChg``, …) to ``1``;
    everything else — including unmodelled protocol markers like ``Cycle``,
    ``Pulse``, ``SIM`` and ``Control`` — is treated as a rest. Matching
    ``_DChg`` before ``_Chg`` is load-bearing: ``CC_DChg`` ends with both
    suffixes and must route to discharge.
    """
    if not isinstance(status, str):
        return "R"
    if status.endswith("_DChg"):
        return 1
    if status.endswith("_Chg"):
        return 0
    return "R"


def _rebuild_cycles(df: pd.DataFrame) -> None:
    """Recompute ``cycle change`` / ``half cycle`` / ``full cycle`` in place.

    navani's exact cycle-change → half-cycle algorithm: rest rows never carry a
    cycle change, so the diff only fires across true charge↔discharge
    transitions; the first active row starts half cycle 1 (rows before it are
    half 0 → full cycle 0, navani's leading partial cycle).
    """
    df["cycle change"] = False
    not_rest = df.index[df["state"] != "R"]
    df.loc[not_rest, "cycle change"] = (
        df.loc[not_rest, "state"].ne(df.loc[not_rest, "state"].shift())
    )
    df["half cycle"] = df["cycle change"].astype(bool).cumsum().astype(int)
    df["full cycle"] = np.ceil(df["half cycle"].to_numpy() / 2).astype(int)


def _accumulate_resets(values: object) -> np.ndarray:
    """Stitch a per-step-resetting counter into one monotonic series.

    Neware's ``Charge_Capacity`` / ``Discharge_Capacity`` counters restart at
    every *step*, so within one half cycle a CC→CV boundary (or an interleaved
    rest) drops the counter back to ~0. Wherever the series falls, the running
    offset absorbs the value it fell from, so the output keeps climbing from
    where the previous step left off. NaNs are treated as "hold the previous
    value" (they neither advance nor reset the counter).
    """
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return v
    # NaN → previous finite value (leading NaNs → 0) so diffs stay meaningful.
    if np.isnan(v).any():
        idx = np.arange(v.size)
        good = ~np.isnan(v)
        v = v[np.maximum.accumulate(np.where(good, idx, 0))]
        v = np.where(np.isnan(v), 0.0, v)
    offsets = np.zeros_like(v)
    drops = np.where(np.diff(v) < 0)[0]
    offsets[drops + 1] = v[drops]
    return v + np.cumsum(offsets)


def _continuous_time_s(df: pd.DataFrame) -> np.ndarray | None:
    """Monotonic elapsed-seconds array for a raw cycler frame.

    Prefers the absolute ``Timestamp`` column — wall clock, monotonic across
    steps, cycles and stitched channels, immune to the per-step/per-cycle
    resets that Neware's relative ``Time`` suffers. Falls back to whatever
    relative time column exists, absorbing resets by accumulating only its
    non-negative diffs. Returns None when the frame has no time source.
    """
    if "Timestamp" in df.columns:
        ts = pd.to_datetime(df["Timestamp"], errors="coerce")
        if ts.notna().any():
            sec = (ts - ts.dropna().iloc[0]).dt.total_seconds().to_numpy(dtype=float)
            finite = sec[~np.isnan(sec)]
            if finite.size and np.all(np.diff(finite) >= 0):
                return sec

    for col in ("Time", "Step Time / s", "TestTime", "Total Time"):
        if col in df.columns:
            sec = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(sec).any():
                continue
            diffs = np.diff(sec, prepend=sec[0])
            diffs[~(diffs >= 0)] = 0.0  # resets and NaN gaps don't advance time
            return np.cumsum(diffs)
    return None


def _integrate_capacity(
    df: pd.DataFrame,
    *,
    only_half_cycles: set[int] | None = None,
    seed_existing: bool = False,
) -> None:
    """Rebuild ``Capacity`` in place as ∫|I|·dt per half cycle.

    ``dt`` comes from the frame's ``Time`` column, so the result is only as
    good as that axis — see ``_load_neware``, which avoids this path entirely
    for Neware files by reading the cycler's own counters instead.

    During rest the current is ~0 so the curve holds; the first active sample
    of each half is pinned to Q = 0, matching navani's per-half-cycle reset.
    No-op when the frame lacks a current or time column.

    ``only_half_cycles`` restricts the rewrite to those half cycles, and
    ``seed_existing`` starts from the frame's current ``Capacity`` rather than
    zeros. Together they let a mixed-cycler frame keep navani's own capacity
    for the halves that did not come from a Neware file.
    """
    if not {"Current", "Time"}.issubset(df.columns):
        return
    dt = df["Time"].diff().fillna(0.0).clip(lower=0.0)
    dq = (df["Current"].abs() * dt) / 3600.0  # mA · s → mAh
    if seed_existing and "Capacity" in df.columns:
        capacity = df["Capacity"].to_numpy(dtype=float).copy()
    else:
        capacity = np.zeros(len(df), dtype=float)
    for hc, idx in df.groupby("half cycle").groups.items():
        if hc == 0 or (only_half_cycles is not None and hc not in only_half_cycles):
            continue
        seg_states = df.loc[idx, "state"]
        active = idx[seg_states.to_numpy() != "R"]
        if len(active) == 0:
            continue
        first_active = active[0]
        seg_dq = dq.loc[idx].copy()
        # Pin the first active sample to Q = 0 (and any pre-active rests
        # along with it). Matches navani's _reset_capacity_per_half_cycle.
        seg_dq.loc[idx[idx <= first_active]] = 0.0
        capacity[df.index.get_indexer(idx)] = seg_dq.cumsum().to_numpy()
    df["Capacity"] = capacity


def _normalise_neware_state(df: pd.DataFrame) -> pd.DataFrame:
    """Re-derive ``state`` / ``half cycle`` / ``full cycle`` / ``Capacity``
    for Neware data so CV phases stay inside their parent half-cycle.

    No-op unless ``df`` carries a Neware-shaped ``Status`` column. When it
    does, the Neware rows' ``state`` is reclassified from ``Status`` (so CV and
    other unmodelled steps stop reading as ``"unknown"``), then
    ``half cycle`` and ``full cycle`` are rebuilt using the same cumulative-
    diff algorithm navani uses. ``Capacity`` is re-integrated from
    ``|Current| · dt`` within each corrected half cycle, because Neware's
    raw ``Charge_Capacity`` / ``Discharge_Capacity`` columns reset at each
    *step* — so without this the V-Q line jumps back to ``Q = 0`` at every
    CC→CV step boundary inside one charge half.

    Only rows carrying a genuine Neware ``Status`` are reclassified. When a
    Neware ``.ndax`` is stitched together with another cycler's file (e.g. a
    Biologic ``.mpr``) by ``multi_echem_file_loader``, the combined frame has
    ``Status`` populated only for the Neware rows; the other rows have
    ``Status == NaN``. Reclassifying those would map them all to ``"R"``
    (rest), collapsing them out of the half-cycle diff so they silently vanish
    from every capacity-/cycle-domain plot while still appearing in V-vs-t.
    Their navani-assigned ``state`` is preserved instead.
    """
    if "Status" not in df.columns:
        return df
    statuses = df["Status"].astype(str)
    is_neware_row = statuses.isin(_NEWARE_STATUSES)
    if not is_neware_row.any():
        return df

    df = df.copy()
    # Reclassify ONLY the genuine Neware rows; keep navani's own state for any
    # rows that came from a different cycler in a mixed multi-file load.
    reclassified = statuses.map(_classify_neware_status)
    if "state" in df.columns:
        df["state"] = df["state"].where(~is_neware_row, reclassified)
    else:
        df["state"] = reclassified

    # navani's exact cycle-change → half-cycle algorithm, applied to the
    # corrected state column. Rest rows don't carry a cycle change, so the
    # diff only fires across true CC↔DCh transitions.
    _rebuild_cycles(df)

    # A frame that is *entirely* Neware rows (a .nda re-exported through Excel,
    # say) can also have its time axis rebuilt from Timestamp. A mixed stack
    # cannot: only the Neware rows carry a Timestamp, so a global rewrite would
    # write NaT-derived NaNs over the other cycler's perfectly good Time.
    if bool(is_neware_row.all()):
        elapsed = _continuous_time_s(df)
        if elapsed is not None:
            df["Time"] = elapsed

    # Rebuild Capacity by integrating |Current|·dt per corrected half cycle.
    # During rest Current ≈ 0 so the curve stays flat; during CV Current is
    # tapering but non-zero so the curve continues smoothly from where the CC
    # phase left off. Only Neware half cycles are re-integrated, and the
    # existing column is the seed, so half cycles belonging to a non-Neware
    # file (mixed .ndax + .mpr stacks) keep navani's own capacity.
    _integrate_capacity(
        df,
        only_half_cycles=set(df.loc[is_neware_row, "half cycle"].to_numpy()),
        seed_existing=True,
    )

    return df


# ---------------------------------------------------------------------------
# Neware (.nda/.ndax) — read directly, bypassing navani's broken time rebuild
# ---------------------------------------------------------------------------


def _read_neware(path: str | Path) -> pd.DataFrame:
    """The raw ``NewareNDA.read`` call — a seam tests fake with a synthetic frame.

    Imported lazily so a missing NewareNDA only bites when a Neware file is
    actually opened.
    """
    from NewareNDA import read

    # NewareNDA resets its logger level on every read; WARNING keeps the
    # per-file INFO chatter out of the log.
    return read(str(path), log_level="WARNING")


def _load_neware(path: str | Path) -> pd.DataFrame:
    """Load a Neware ``.nda`` / ``.ndax`` into a navani-convention DataFrame.

    Every derived column is built here from the raw file's own ground truth,
    because navani's Neware reader gets the time axis wrong. It rebuilds
    ``Time`` as ``groupby("Step_Index")["Timestamp"].transform("first")`` plus
    the per-step clock (``navani/neware.py:56-57``); ``Step_Index`` is the
    protocol step *definition* number, which repeats every loop, so every
    occurrence of a given step inherits the *first* cycle's base timestamp.
    Within one step the origin is constant so per-row ``dt`` survives, but at
    every step boundary the origin jumps — backwards jumps get clamped away
    (deleting real elapsed time and real charge) and forwards jumps fabricate
    both. Any capacity integrated against that axis inherits the error.

    So instead:

    - ``state`` / ``half cycle`` / ``full cycle`` come from the ``Status``
      strings, so CV phases stay inside their parent half-cycle (navani maps
      only CC steps and spawns a spurious half cycle at each CC↔CV boundary).
    - ``Time`` comes from the absolute ``Timestamp`` — wall clock, monotonic
      across steps and cycles. The raw per-step column is kept under navani's
      name for it, ``Step Time / s``.
    - ``Capacity`` comes from the cycler's own per-step ``Charge_Capacity`` /
      ``Discharge_Capacity`` counters, stitched across step boundaries within
      each half cycle — exact however sparsely the file was sampled. Falls
      back to ∫|I|·dt when a file carries no counter columns.
    """
    df = _read_neware(path)
    df = df.reset_index(drop=True)

    if "Current(mA)" in df.columns:
        df["Current"] = df["Current(mA)"]
    if "Status" not in df.columns:
        raise ValueError(f"Neware file has no Status column: {path}")
    df["state"] = df["Status"].astype(str).map(_classify_neware_status)
    _rebuild_cycles(df)

    # Continuous time; keep the raw per-step column under navani's name for it.
    if "Time" in df.columns:
        df = df.rename(columns={"Time": "Step Time / s"})
    elapsed = _continuous_time_s(df)
    if elapsed is None:
        raise ValueError(f"Neware file has no usable time column: {path}")
    df["Time"] = elapsed

    counter_cols = {"Charge_Capacity(mAh)", "Discharge_Capacity(mAh)"}
    if counter_cols.issubset(df.columns):
        combined = (
            df["Charge_Capacity(mAh)"].astype(float)
            + df["Discharge_Capacity(mAh)"].astype(float)
        ).to_numpy()
        capacity = np.zeros(len(df), dtype=float)
        for hc, idx in df.groupby("half cycle").groups.items():
            if hc == 0:
                continue
            pos = df.index.get_indexer(idx)
            capacity[pos] = _accumulate_resets(combined[pos])
        df["Capacity"] = capacity
        _warn_if_capacity_units_look_wrong(df, path)
    else:
        _integrate_capacity(df)
    return df


def _warn_if_capacity_units_look_wrong(df: pd.DataFrame, path: str | Path) -> None:
    """Log when the counter column disagrees wildly with ∫|I|·dt.

    navani carries an ``expected_capacity_unit`` knob because some Neware
    machines write Ah into a column named ``(mAh)``. We read the counters at
    face value, so a mislabelled machine would come out 1000x low with no other
    signal. Comparing against the current integral catches that.
    """
    if not {"Current", "Time", "Capacity"}.issubset(df.columns):
        return
    dt = df["Time"].diff().fillna(0.0).clip(lower=0.0)
    integrated = float(((df["Current"].abs() * dt) / 3600.0).sum())
    counted = float(df["Capacity"].max())
    if integrated <= 0 or counted <= 0:
        return
    ratio = integrated / counted
    if ratio > 5.0 or ratio < 0.2:
        logger.warning(
            "%s: capacity counters (max %.4g mAh) disagree with the current "
            "integral (%.4g mAh) by %.0fx — check the cycler's capacity units.",
            path,
            counted,
            integrated,
            max(ratio, 1 / ratio),
        )


def _load_via_navani(path: str | Path) -> pd.DataFrame:
    """One non-Neware file through navani, plus Neware-shape normalisation for
    Neware data that arrives re-exported (e.g. via Excel)."""
    return _normalise_neware_state(ec.echem_file_loader(str(path)))


def _load_one(path: str | Path) -> pd.DataFrame:
    if str(path).lower().endswith(_NEWARE_EXTENSIONS):
        return _load_neware(path)
    return _load_via_navani(path)


def _stitch(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate per-file frames into one continuous run.

    Each input frame already carries correct per-file columns; stitching makes
    ``Time`` continuous across the boundary (each file offset to start where
    the previous ended), rebuilds half/full cycles globally from ``state`` (so
    a half cycle spanning a file boundary merges instead of double-counting),
    and re-offsets ``Capacity`` within each merged half (a continuing file's
    capacity restarts at 0 — the same reset-absorption used for Neware's
    per-step counters).

    Note that idle time *between* files is not preserved: file ``n+1`` starts
    where file ``n`` ended. For cycling analysis that is what you want; if a
    calendar-ageing gap ever needs to survive, key the offset off the frames'
    absolute ``Timestamp`` instead.
    """
    shifted = []
    offset = 0.0
    for f in frames:
        f = f.copy()
        if "Time" in f.columns and len(f):
            t = pd.to_numeric(f["Time"], errors="coerce")
            start = t.dropna().iloc[0] if t.notna().any() else 0.0
            f["Time"] = t - start + offset
            end = t.dropna().iloc[-1] if t.notna().any() else start
            offset += float(end - start)
        shifted.append(f)
    df = pd.concat(shifted, ignore_index=True)

    if "state" in df.columns:
        _rebuild_cycles(df)
        if "Capacity" in df.columns:
            capacity = np.zeros(len(df), dtype=float)
            cap_vals = pd.to_numeric(df["Capacity"], errors="coerce").to_numpy()
            for hc, idx in df.groupby("half cycle").groups.items():
                if hc == 0:
                    continue
                pos = df.index.get_indexer(idx)
                capacity[pos] = _accumulate_resets(cap_vals[pos])
            df["Capacity"] = capacity
    return df


def stitch_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Stitch already-loaded per-file frames into one continuous run.

    The public entry point to ``_stitch`` for callers that parsed each file
    separately and want to avoid re-reading them. A single frame passes
    through unchanged; an empty list is a programming error.
    """
    if not frames:
        raise ValueError("stitch_frames called with no frames")
    return frames[0] if len(frames) == 1 else _stitch(frames)



def cycle_summary(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Per-cycle summary DataFrame: ``cycle`` / ``Charge_mAh`` /
    ``Discharge_mAh`` / ``Charge_Ah`` / ``Discharge_Ah`` / ``CE``.

    A cycle's charge (discharge) capacity is the max ``Capacity`` reached on
    its charge-state (discharge-state) rows — navani's convention, with
    **charge = state 0 and discharge = state 1**. ``CE`` = discharge ÷ charge,
    NaN when the cycle has no charge. Cycle 0 (rows before the first active
    step) comes through with NaN capacities, matching navani's leading partial
    cycle; callers drop it.

    Deliberately does *not* call ``navani.echem.cycle_summary``: that function
    writes ``full cycle`` back onto the frame it is handed
    (``navani/echem.py:729``), flipping the column from int to float on the
    caller's DataFrame — which the GUI keeps in its ``raw_data`` parse cache.
    This implementation is pure.
    """
    df = raw_df
    if "full cycle" not in df.columns:
        if "half cycle" not in df.columns:
            raise ValueError("DataFrame has neither 'half cycle' nor 'full cycle' columns")
        full = np.ceil(pd.to_numeric(df["half cycle"]).to_numpy() / 2)
    else:
        full = pd.to_numeric(df["full cycle"], errors="coerce").to_numpy(dtype=float)

    state = df["state"] if "state" in df.columns else pd.Series("R", index=df.index)
    if "Capacity" in df.columns:
        cap = pd.to_numeric(df["Capacity"], errors="coerce")
    else:
        cap = pd.Series(np.nan, index=df.index)

    work = pd.DataFrame(
        {
            "cycle": full,
            "cap": cap.to_numpy(),
            "is_charge": (state == 0).to_numpy(),
            "is_discharge": (state == 1).to_numpy(),
        }
    ).dropna(subset=["cycle"])
    if work.empty:
        raise ValueError("no cycles found in the data file")

    rows = []
    for cyc, seg in work.groupby("cycle", sort=True):
        rows.append(
            {
                "cycle": int(cyc),
                "Charge_mAh": seg.loc[seg["is_charge"], "cap"].max(),
                "Discharge_mAh": seg.loc[seg["is_discharge"], "cap"].max(),
            }
        )
    out = pd.DataFrame(rows, columns=["cycle", "Charge_mAh", "Discharge_mAh"])
    out["Charge_Ah"] = out["Charge_mAh"] / 1000.0
    out["Discharge_Ah"] = out["Discharge_mAh"] / 1000.0
    out["CE"] = (out["Discharge_mAh"] / out["Charge_mAh"]).where(out["Charge_mAh"] > 0)
    return out


def filter_by_cycle(
    df: pd.DataFrame, cycles: int | list[int] | None
) -> pd.DataFrame:
    """Filter a navani DataFrame to the given full-cycle numbers.

    Uses the ``full cycle`` column directly (matching ``cycle_summary``'s
    convention). The previous ``half cycle``-formula fallback ``(2c-1, 2c)``
    misbehaved for cells whose first half-cycle is a discharge -- those
    cells start at half_cycle=0, and the formula picked the wrong halves.
    """
    if cycles is None:
        return df
    if isinstance(cycles, int):
        cycles = [cycles]
    cycles = sorted({int(c) for c in cycles if c > 0})

    if "full cycle" in df.columns:
        return df[df["full cycle"].isin(cycles)].copy()
    if "half cycle" in df.columns:
        half_cycles = [h for c in cycles for h in (2 * c - 1, 2 * c)]
        return df[df["half cycle"].isin(half_cycles)].copy()
    raise ValueError("DataFrame has neither 'half cycle' nor 'full cycle' columns")


def split_half_cycles(
    df: pd.DataFrame, x_col: str, y_col: str
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(x, y)`` arrays with NaN separators between half-cycle segments.

    Use this when plotting V-Q, dQ/dV, etc. as a single line per cell. Within
    one ``full cycle`` the ``Capacity`` column resets at each half-cycle
    boundary; without the NaN gap the line draws a spurious connector
    between end-of-charge and start-of-discharge. Both matplotlib and plotly
    break lines at NaN.
    """
    if df.empty:
        return np.empty(0), np.empty(0)
    if "half cycle" not in df.columns:
        return df[x_col].to_numpy(), df[y_col].to_numpy()
    xs: list[float] = []
    ys: list[float] = []
    first = True
    for _, seg in df.groupby("half cycle", sort=True):
        if seg.empty:
            continue
        if not first:
            xs.append(np.nan)
            ys.append(np.nan)
        xs.extend(seg[x_col].to_numpy().tolist())
        ys.extend(seg[y_col].to_numpy().tolist())
        first = False
    return np.asarray(xs), np.asarray(ys)


STATUS_COLUMN_CANDIDATES = ("Status", "status", "Step_Type", "state")


def detect_status_column(df: pd.DataFrame) -> str | None:
    """Return the first status-like column in ``df`` with more than one
    distinct value, or ``None`` if no usable column exists.

    Preference order matches the richness of the labels:

    * ``Status`` — Neware's native step-type strings (``CC_Chg``, ``CV_Chg``,
      ``Rest``, …).
    * ``Step_Type`` — Lanndt's analogous column.
    * ``state`` — navani's standardised column (``R`` / 0 / 1 / "unknown").
    """
    for col in STATUS_COLUMN_CANDIDATES:
        if col in df.columns and df[col].astype(str).nunique() > 1:
            return col
    return None


def split_by_status(
    df: pd.DataFrame, x_col: str, y_col: str, status_col: str
) -> list[tuple[np.ndarray, np.ndarray, str]]:
    """Split a DataFrame into contiguous runs of equal ``status_col`` value.

    Returns a list of ``(x_arr, y_arr, status_str)`` triples in time order.
    Each run includes a copy of the previous run's final point as its first
    point, so a downstream renderer plots one trace per run and adjacent
    runs touch at the colour transition (no visual gap).
    """
    if df.empty or status_col not in df.columns:
        return []
    df = df.reset_index(drop=True)
    status_str = df[status_col].astype(str)
    run_ids = (status_str != status_str.shift()).cumsum()
    runs: list[tuple[np.ndarray, np.ndarray, str]] = []
    prev_last_x: float | None = None
    prev_last_y: float | None = None
    for _, run_df in df.groupby(run_ids, sort=False):
        if run_df.empty:
            continue
        xs = run_df[x_col].to_numpy()
        ys = run_df[y_col].to_numpy()
        sval = str(run_df[status_col].iloc[0])
        if prev_last_x is not None and prev_last_y is not None:
            xs = np.concatenate(([prev_last_x], xs))
            ys = np.concatenate(([prev_last_y], ys))
        runs.append((xs, ys, sval))
        prev_last_x = run_df[x_col].iloc[-1]
        prev_last_y = run_df[y_col].iloc[-1]
    return runs


def compute_dqdv(
    df: pd.DataFrame,
    *,
    mode: str = "dQ/dV",
    smoothing: bool = True,
    polynomial_spline: int = 3,
    s_spline: float = 1e-5,
    window_size_1: int = 101,
    window_size_2: int = 1001,
    polyorder_1: int = 5,
    polyorder_2: int = 5,
) -> pd.DataFrame:
    """Compute dQ/dV or dV/dQ per half cycle using navani.

    Ported from datalab (``pydatalab/apps/echem/utils.py:compute_gpcl_differential``),
    https://github.com/datalab-org/datalab — MIT-licensed; see the NOTICE file
    at the repository root.

    Returns a DataFrame with columns ``capacity (mAh)`` (or ``voltage (V)`` for
    ``dV/dQ``), the conjugate column, the derivative, and ``full cycle`` /
    ``half cycle`` for each row.
    """
    if len(df) < 2:
        return df.iloc[0:0].copy()

    if mode.lower().replace("/", "") == "dvdq":
        y_label = "voltage (V)"
        x_label = "capacity (mAh)"
        yp_label = "dV/dQ (V/mA)"
    else:
        y_label = "capacity (mAh)"
        x_label = "voltage (V)"
        yp_label = "dQ/dV (mA/V)"

    # navani's df uses 'Voltage' / 'Capacity'; rename locally for the call.
    local = df.rename(columns={"Voltage": "voltage (V)", "Capacity": "capacity (mAh)"})

    smoothing_parameters = {
        "polynomial_spline": polynomial_spline,
        "s_spline": s_spline,
        "window_size_1": window_size_1 if window_size_1 % 2 else window_size_1 + 1,
        "window_size_2": window_size_2 if window_size_2 % 2 else window_size_2 + 1,
        "polyorder_1": polyorder_1,
        "polyorder_2": polyorder_2,
        "final_smooth": smoothing,
    }

    out_frames: list[pd.DataFrame] = []
    for hc in local["half cycle"].unique():
        seg = local[local["half cycle"] == hc]
        try:
            x, yp, y = ec.dqdv_single_cycle(seg[y_label], seg[x_label], **smoothing_parameters)
        except (TypeError, ValueError):
            # rests / voltage holds / segments too short
            continue
        cycle_index = int(seg["full cycle"].max())
        out_frames.append(
            pd.DataFrame(
                {
                    x_label: x,
                    y_label: y,
                    yp_label: yp,
                    "full cycle": np.full(len(x), cycle_index, dtype=int),
                    "half cycle": np.full(len(x), int(hc), dtype=int),
                }
            )
        )

    if not out_frames:
        return pd.DataFrame(columns=[x_label, y_label, yp_label, "full cycle", "half cycle"])
    return pd.concat(out_frames, ignore_index=True)
