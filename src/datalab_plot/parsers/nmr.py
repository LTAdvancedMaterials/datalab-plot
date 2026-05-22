"""Parse 1D NMR spectra.

Ported from datalab (``pydatalab/apps/nmr/utils.py``),
https://github.com/datalab-org/datalab — MIT-licensed; see the NOTICE file
at the repository root.
"""
from __future__ import annotations

import logging
import tempfile
import warnings
import zipfile
from pathlib import Path

import nmrglue as ng
import pandas as pd

logger = logging.getLogger(__name__)

NMR_EXTENSIONS = (".zip", ".jdx", ".dx")


def is_nmr_file(file_meta: dict) -> bool:
    name = (file_meta.get("name") or "").lower()
    return any(name.endswith(ext) for ext in NMR_EXTENSIONS)


def _read_bruker_dir(
    data_dir: Path, process_number: int = 1
) -> tuple[pd.DataFrame, dict, str | None]:
    a_dic, a_data = ng.fileio.bruker.read(str(data_dir))
    pdata_dir = data_dir / "pdata" / str(process_number)
    try:
        p_dic, p_data = ng.fileio.bruker.read_pdata(str(pdata_dir))
    except Exception:
        # No processed data on disk — fall through to FFT-from-FID below.
        logger.debug("read_pdata failed for %s; will process raw FID", pdata_dir)
        p_dic, p_data = None, None

    title = None
    title_file = pdata_dir / "title"
    if title_file.exists():
        title = title_file.read_text()

    nscans = a_dic["acqus"]["NS"]

    if p_dic is None:
        udic = ng.bruker.guess_udic(a_dic, a_data)
        uc = ng.fileiobase.uc_from_udic(udic)
        if udic[0]["time"]:
            warnings.warn(
                "Bruker project has no processed data; running FFT + ACME autophase.",
                stacklevel=2,
            )
            try:
                a_data = ng.bruker.remove_digital_filter(a_dic, a_data)
            except Exception:
                # Digital-filter metadata absent/unsupported — proceed with
                # the raw FID; the FFT below still produces a usable spectrum.
                logger.debug("remove_digital_filter failed; using raw FID", exc_info=True)
            p_data = ng.process.proc_base.fft(a_data)
        else:
            p_data = a_data
        p_data = ng.process.proc_base.rev(p_data)
        p_data = ng.process.proc_autophase.autops(p_data, "acme")
        p_data = ng.process.proc_base.di(p_data)
    else:
        udic = ng.bruker.guess_udic(p_dic, p_data)
        uc = ng.fileiobase.uc_from_udic(udic)

    df = pd.DataFrame(
        {
            "ppm": uc.ppm_scale(),
            "hz": uc.hz_scale(),
            "intensity": p_data,
            "intensity_per_scan": p_data / nscans,
        }
    )
    return df, a_dic, title


def _find_bruker_root(extracted: Path) -> Path:
    """A Bruker project is a directory containing `acqus`. Find it inside the unzipped tree."""
    for p in extracted.rglob("acqus"):
        return p.parent
    raise RuntimeError(f"No Bruker `acqus` file found inside {extracted}")


def load_nmr(path: Path) -> tuple[pd.DataFrame, dict]:
    """Parse a 1D NMR spectrum from a Bruker zip or JCAMP-DX file.

    Returns ``(df, metadata)`` where ``df`` has columns ``ppm, hz, intensity,
    intensity_per_scan`` and ``metadata`` is a dict of acquisition parameters.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with tempfile.TemporaryDirectory() as td:
            with zipfile.ZipFile(path) as zf:
                zf.extractall(td)
            root = _find_bruker_root(Path(td))
            df, dic, title = _read_bruker_dir(root)
        return df, {"title": title, "nscans": dic.get("acqus", {}).get("NS"), "raw": dic}
    if suffix in (".jdx", ".dx"):
        dic, data = ng.fileio.jcampdx.read(str(path))
        udic = ng.jcampdx.guess_udic(dic, data)
        uc = ng.fileiobase.uc_from_udic(udic)
        nscans = int(dic.get("$NS", [1])[0]) if "$NS" in dic else 1
        title = dic.get("TITLE", "")
        df = pd.DataFrame(
            {
                "ppm": uc.ppm_scale(),
                "hz": uc.hz_scale(),
                "intensity": data,
                "intensity_per_scan": data / nscans,
            }
        )
        return df, {"title": title, "nscans": nscans, "raw": dic}
    if suffix == ".jdf":
        raise NotImplementedError("JEOL JDF (.jdf) not yet supported")
    raise ValueError(f"Unrecognised NMR file extension: {suffix}")
