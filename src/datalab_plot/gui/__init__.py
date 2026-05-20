"""Streamlit GUI package for datalab-plot.

The app is launched via ``datalab-plot gui`` (see :mod:`datalab_plot.cli`),
which runs :mod:`datalab_plot.gui.app` as a Streamlit script.
"""
from __future__ import annotations

from datalab_plot.gui.app import main

__all__ = ("main",)
