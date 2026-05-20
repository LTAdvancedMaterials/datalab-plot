"""Smoke tests for the XRD and UV-Vis parsers.

These write small synthetic files in each format (no committed data) and check
the parsers return well-formed DataFrames. NMR parsing needs real Bruker /
JCAMP binaries to exercise meaningfully, so only its file-type predicate is
tested here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from datalab_plot.parsers.nmr import is_nmr_file
from datalab_plot.parsers.uvvis import find_absorbance, is_uvvis_file, parse_uvvis_txt
from datalab_plot.parsers.xrd import is_xrd_file, load_xrd


def test_load_xrd_two_column(tmp_path):
    p = tmp_path / "pattern.xy"
    p.write_text("# a comment line\n10.0 100\n10.1 150\n10.2 120\n")
    df = load_xrd(p)
    assert list(df.columns) == ["twotheta", "intensity"]
    assert len(df) == 3


def test_load_xrd_three_column_adds_error(tmp_path):
    p = tmp_path / "pattern.xye"
    p.write_text("10.0 100 5\n10.1 150 6\n")
    df = load_xrd(p)
    assert list(df.columns) == ["twotheta", "intensity", "error"]


def test_load_xrd_xrdml(tmp_path):
    p = tmp_path / "scan.xrdml"
    p.write_text(
        "<xrdMeasurement>"
        "<startPosition>10.0</startPosition>\n  <endPosition>20.0</endPosition>"
        '<intensities unit="counts">100 200 300 250</intensities>'
        "</xrdMeasurement>"
    )
    df = load_xrd(p)
    assert list(df.columns) == ["twotheta", "intensity"]
    assert len(df) == 4
    assert df["twotheta"].iloc[0] == 10.0
    assert df["twotheta"].iloc[-1] == 20.0


def test_is_xrd_file():
    assert is_xrd_file({"name": "scan.xrdml"})
    assert not is_xrd_file({"name": "scan.mpr"})


def test_parse_uvvis_txt(tmp_path):
    p = tmp_path / "spectrum.txt"
    header = "\n".join(f"header {i}" for i in range(7))
    p.write_text(header + "\n400;10;1;100\n401;12;1;102\n")
    df = parse_uvvis_txt(p)
    assert list(df.columns) == [
        "Wavelength", "Sample counts", "Dark counts", "Reference counts"
    ]
    assert len(df) == 2


def test_find_absorbance():
    sample = pd.DataFrame({"Wavelength": [400, 401], "Sample counts": [10.0, 1.0]})
    ref = pd.DataFrame({"Wavelength": [400, 401], "Sample counts": [100.0, 10.0]})
    out = find_absorbance(sample, ref)
    assert list(out.columns) == ["Wavelength", "Absorbance"]
    # A = -log10(10/100) = 1.0 ; -log10(1/10) = 1.0
    np.testing.assert_allclose(out["Absorbance"].to_numpy(), [1.0, 1.0])


def test_is_uvvis_file():
    assert is_uvvis_file({"name": "uvvis.txt"})
    assert not is_uvvis_file({"name": "uvvis.csv"})


def test_is_nmr_file():
    assert is_nmr_file({"name": "spectrum.zip"})
    assert is_nmr_file({"name": "proton.jdx"})
    assert not is_nmr_file({"name": "proton.mpr"})
