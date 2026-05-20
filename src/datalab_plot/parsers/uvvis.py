"""Parse UV-Vis ASCII exports. Ported from pydatalab apps/uvvis/utils.py."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

UVVIS_EXTENSIONS = (".txt", ".raw8.txt")


def is_uvvis_file(file_meta: dict) -> bool:
    name = (file_meta.get("name") or "").lower()
    return name.endswith(".txt")


def parse_uvvis_txt(path: Path) -> pd.DataFrame:
    """Parse a UV-Vis ``.txt`` export.

    Format: 7-line header, then semicolon-separated columns
    ``Wavelength, Sample counts, Dark counts, Reference counts``.
    """
    df = pd.read_csv(path, sep=";", skiprows=7, header=None)
    df.columns = ["Wavelength", "Sample counts", "Dark counts", "Reference counts"]
    return df


def find_absorbance(data_df: pd.DataFrame, reference_df: pd.DataFrame) -> pd.DataFrame:
    """Compute absorbance ``A = -log10(I_sample / I_ref)``."""
    absorbance = -np.log10(data_df["Sample counts"] / reference_df["Sample counts"])
    return pd.DataFrame({"Wavelength": data_df["Wavelength"], "Absorbance": absorbance})
