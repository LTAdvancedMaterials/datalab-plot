"""Parse electrochemical cycling files via navani and compute derived quantities."""
from __future__ import annotations

import functools
import logging
import warnings
from pathlib import Path

import navani.echem as ec
import numpy as np
import pandas as pd

# Suppress the per-file INFO chatter from NewareNDA (.nda/.ndax). Two
# independent defenses, because NewareNDA's read() resets the logger level on
# every call from its log_level='INFO' default, and any rich/pydatalab-style
# logging config can later flip it back:
#
#   1. Monkey-patch NewareNDA.NewareNDA.read so navani's positional call uses
#      log_level='WARNING'.
#   2. Attach a Filter to the 'newarenda' logger that drops any record below
#      WARNING regardless of the logger's nominal level. Filters survive
#      setLevel() calls, so this catches resets we couldn't anticipate.
class _DropInfoFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return record.levelno >= logging.WARNING


_newarenda_logger = logging.getLogger("newarenda")
_newarenda_logger.setLevel(logging.WARNING)
if not any(isinstance(f, _DropInfoFilter) for f in _newarenda_logger.filters):
    _newarenda_logger.addFilter(_DropInfoFilter())

try:
    import NewareNDA.NewareNDA as _newaremod

    if not getattr(_newaremod, "_datalab_plot_patched", False):
        _orig_read = _newaremod.read

        @functools.wraps(_orig_read)
        def _quiet_read(file, *args, log_level="WARNING", **kwargs):
            return _orig_read(file, *args, log_level=log_level, **kwargs)

        _newaremod.read = _quiet_read
        _newaremod._datalab_plot_patched = True
except ImportError:
    pass


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
    """Load one or more cycler files with navani and return the raw DataFrame.

    For multiple files, ``navani.echem.multi_echem_file_loader`` stitches them
    together with cycle-index offsets.
    """
    if isinstance(paths, (str, Path)):
        return ec.echem_file_loader(str(paths))
    paths = list(paths)
    if len(paths) == 1:
        return ec.echem_file_loader(str(paths[0]))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                "Capacity columns are not equal, replacing with new capacity column"
            ),
            category=UserWarning,
        )
        return ec.multi_echem_file_loader([str(p) for p in paths])


def cycle_summary(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Return a per-cycle summary DataFrame.

    Adds ``Charge_mAh``, ``Discharge_mAh`` and ``CE`` (discharge/charge ratio).
    Uses ``navani.echem.cycle_summary`` when available, falling back to a
    grouped max() on ``full cycle`` -- which is the convention navani's
    parsed dataframes follow (``Capacity`` is cumulative within a half cycle
    and reset between cycles).
    """
    try:
        summary = ec.cycle_summary(raw_df)
    except Exception:
        summary = None

    if summary is not None and "Charge Capacity" in summary.columns:
        out = pd.DataFrame(
            {
                "cycle": pd.to_numeric(summary.index, downcast="integer"),
                "Charge_Ah": summary["Charge Capacity"],
                "Discharge_Ah": summary["Discharge Capacity"],
            }
        ).reset_index(drop=True)
    else:
        # Fallback: aggregate by full cycle. navani guarantees a "full cycle"
        # column. Capacity is in mAh in navani's standard output -- treat it
        # as mAh and convert to Ah for consistency with `summary`.
        g = (
            raw_df.dropna(subset=["full cycle"])
            .groupby("full cycle")
            .agg(
                Charge_mAh=("Capacity", lambda s: s[raw_df.loc[s.index, "state"].eq(1)].max()
                            if "state" in raw_df.columns
                            else s.max()),
                Discharge_mAh=(
                    "Capacity",
                    lambda s: s[raw_df.loc[s.index, "state"].eq(0)].max()
                    if "state" in raw_df.columns
                    else s.max(),
                ),
            )
            .reset_index()
            .rename(columns={"full cycle": "cycle"})
        )
        g["Charge_Ah"] = g["Charge_mAh"] / 1000.0
        g["Discharge_Ah"] = g["Discharge_mAh"] / 1000.0
        out = g[["cycle", "Charge_Ah", "Discharge_Ah"]]

    out["Charge_mAh"] = out["Charge_Ah"] * 1000.0
    out["Discharge_mAh"] = out["Discharge_Ah"] * 1000.0
    out["CE"] = (out["Discharge_Ah"] / out["Charge_Ah"]).where(out["Charge_Ah"] > 0)
    out["cycle"] = out["cycle"].astype(int)
    return out.reset_index(drop=True)


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

    Ported from
    upstream `datalab` server (pydatalab/apps/echem/utils.py:compute_gpcl_differential).
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
