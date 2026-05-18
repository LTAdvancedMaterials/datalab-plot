"""Parse XRD patterns. Ported from pydatalab apps/xrd/utils.py."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd


XRD_EXTENSIONS = (".xy", ".xye", ".dat", ".xrdml")
_STARTEND_REGEX = (
    r"<startPosition>(\d+\.\d+)</startPosition>\s+<endPosition>(\d+\.\d+)</endPosition>"
)
_DATA_REGEX = r'<(intensities|counts) unit="counts">((-?\d+ )+-?\d+)</(intensities|counts)>'


def is_xrd_file(file_meta: dict) -> bool:
    name = (file_meta.get("name") or "").lower()
    return any(name.endswith(ext) for ext in XRD_EXTENSIONS)


def _parse_xrdml(path: Path) -> pd.DataFrame:
    s = path.read_text()
    m = re.search(_STARTEND_REGEX, s)
    if not m:
        raise ValueError(f"{path}: start/end positions not found in XRDML")
    start, end = float(m.group(1)), float(m.group(2))
    m2 = re.search(_DATA_REGEX, s)
    if not m2:
        raise ValueError(f"{path}: intensities not found in XRDML")
    intensities = [float(x) for x in m2.group(2).split()]
    angles = np.linspace(start, end, num=len(intensities))
    return pd.DataFrame({"twotheta": angles, "intensity": intensities})


def _parse_two_column(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        sep=r"\s+",
        header=None,
        comment="#",
        engine="python",
    )
    if df.shape[1] < 2:
        raise ValueError(f"{path}: expected at least 2 whitespace-separated columns")
    out = pd.DataFrame({"twotheta": df.iloc[:, 0], "intensity": df.iloc[:, 1]})
    if df.shape[1] >= 3:
        out["error"] = df.iloc[:, 2]
    return out


def load_xrd(path: Path) -> pd.DataFrame:
    """Parse an XRD pattern into a DataFrame with ``twotheta`` and ``intensity``.

    Supported: ``.xy``, ``.xye``, ``.dat`` (whitespace-separated 2-3 columns)
    and ``.xrdml`` (Panalytical). Other formats raise :class:`NotImplementedError`.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xrdml":
        return _parse_xrdml(path)
    if suffix in (".xy", ".xye", ".dat"):
        return _parse_two_column(path)
    raise NotImplementedError(
        f"XRD format {suffix!r} not yet supported (have you got an .xrdml/.xy/.xye/.dat instead?)"
    )
