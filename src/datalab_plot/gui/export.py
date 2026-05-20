"""Figure export: long-format CSV of the current plot's traces, and the
PNG-export caption (PNG itself comes from the Plotly modebar camera button).
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import streamlit as st

from datalab_plot.gui.constants import PlotStyle

logger = logging.getLogger(__name__)


def _figure_to_csv(fig, style: PlotStyle | None = None) -> str:
    """Long-format CSV of every visible line trace in the current figure.

    Columns: ``trace, x, y``. Legend-only / colorbar-only placeholder
    traces (whose x/y are a single None) are skipped.

    When ``style`` carries manual axis limits, points outside the
    visible window are dropped so the export matches what's on screen.
    Streamlit can't read back a mouse-zoom range, so the axis-limit
    fields are the only "visible range" the app actually knows about —
    when they're left on auto, the full data is exported.
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
        # Skip the invisible legend / colorbar placeholder traces.
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


def _png_export_section(style: PlotStyle | None = None) -> None:
    """Export controls for the current figure.

    PNG: the interactive chart's modebar camera button downloads the live
    figure directly (pixel-accurate, high-res via the chart's scale=3
    config). CSV: the data behind every visible trace, long-format,
    clipped to the manual axis limits when those are set.
    """
    fig = st.session_state.get("last_fig")
    if fig is None:
        return
    cols = st.columns([3, 1])
    cols[0].caption(
        "📷 **PNG** — hover the plot and click the **camera** icon in its "
        "top-right toolbar (high-res, exactly what you see). **CSV** →"
    )
    try:
        csv = _figure_to_csv(fig, style)
    except Exception:
        logger.warning("CSV export failed", exc_info=True)
        csv = ""
    cols[1].download_button(
        "Download CSV",
        data=csv,
        file_name="datalab_plot.csv",
        mime="text/csv",
        width="stretch",
        disabled=not csv,
        help=(
            "The data behind every line in the current plot (long format), "
            "clipped to the manual axis limits when those are set."
        ),
    )
