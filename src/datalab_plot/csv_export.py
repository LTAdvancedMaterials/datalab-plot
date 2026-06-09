"""Long-format CSV of every line trace in a Plotly figure.

Used by both GUI back-ends (Streamlit ``gui/export.py`` and Dash
``gui_dash/export.py``). Pure — no UI framework imports.
"""
from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd

from datalab_plot.plot_constants import PlotStyle


def figure_to_csv(fig, style: PlotStyle | None = None) -> str:
    """Long-format CSV of every visible line trace in ``fig``.

    Columns: ``trace, x, y``. Legend-only / colorbar-only placeholder
    traces (whose x/y are a single None) are skipped.

    When ``style`` carries manual axis limits, points outside the visible
    window are dropped so the export matches what's on screen.
    """
    sx_min = sx_max = sy_min = sy_max = sy2_min = sy2_max = None
    if style is not None:
        sx_min, sx_max = style.x_min, style.x_max
        sy_min, sy_max = style.y_min, style.y_max
        sy2_min, sy2_max = style.y2_min, style.y2_max

    def _in(v, lo, hi) -> bool:
        if v is None or lo is None or hi is None:
            return True
        try:
            return lo <= float(v) <= hi
        except (TypeError, ValueError):
            return True

    rows: list[dict[str, Any]] = []
    for tr in fig.data:
        tx = getattr(tr, "x", None)
        ty = getattr(tr, "y", None)
        xs = list(tx) if tx is not None else []
        ys = list(ty) if ty is not None else []
        if not xs or not ys:
            continue
        if all(v is None for v in xs):
            continue
        name = tr.name or "trace"
        on_y2 = getattr(tr, "yaxis", None) == "y2"
        ylo, yhi = (sy2_min, sy2_max) if on_y2 else (sy_min, sy_max)
        for x, y in zip(xs, ys, strict=False):
            if not _in(x, sx_min, sx_max) or not _in(y, ylo, yhi):
                continue
            rows.append({"trace": name, "x": x, "y": y})
    return pd.DataFrame(rows, columns=["trace", "x", "y"]).to_csv(index=False)


def figure_to_csv_tabs(tabs: list, style: PlotStyle | None = None) -> str:
    """Long-format CSV for a list of ``(tab_title, figure)`` pairs.

    Same schema as :func:`figure_to_csv` plus a ``tab`` column.
    """
    frames: list[pd.DataFrame] = []
    for tab_title, fig in tabs:
        df = pd.read_csv(StringIO(figure_to_csv(fig, style)))
        if not df.empty:
            df.insert(0, "tab", tab_title)
            frames.append(df)
    if not frames:
        return ""
    return pd.concat(frames, ignore_index=True).to_csv(index=False)
