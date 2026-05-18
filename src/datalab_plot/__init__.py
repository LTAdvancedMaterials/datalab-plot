"""datalab-plot: plot data from a datalab instance locally."""
from __future__ import annotations

from .client import DatalabPlotClient
from .plots.echem import plot_cell, plot_cycles
from .plots.nmr import plot_nmr
from .plots.uvvis import plot_uvvis
from .plots.xrd import plot_xrd
from .search import find_cells

__all__ = (
    "DatalabPlotClient",
    "find_cells",
    "plot_cell",
    "plot_cycles",
    "plot_nmr",
    "plot_uvvis",
    "plot_xrd",
)
