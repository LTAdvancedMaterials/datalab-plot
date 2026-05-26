"""Small pure helpers shared across the GUI package — colour conversion,
axis-machinery lookups, picker-frame construction. No Streamlit here.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from datalab_plot.gui.constants import _AXIS_TABLE, PICKER_COLUMNS, STATUS_COLOR_MAP
from datalab_plot.series import cumulative_time_hours


def _rgba_to_css(c: Any) -> str:
    from matplotlib.colors import to_rgba

    r, g, b, a = to_rgba(c)
    return f"rgba({int(r * 255)}, {int(g * 255)}, {int(b * 255)}, {a:.3f})"


def _desaturate_css(c: Any, amount: float = 0.5) -> str:
    """Desaturate colour ``c`` by ``amount`` (0 = unchanged, 1 = greyscale) and
    return a CSS rgba string. Mixes each channel toward the luminance-weighted
    grey so hue is preserved while chroma drops — useful for secondary-axis
    traces that should read as supporting data without losing their cell colour.
    """
    from matplotlib.colors import to_rgba

    r, g, b, a = to_rgba(c)
    grey = 0.299 * r + 0.587 * g + 0.114 * b
    r2 = r + (grey - r) * amount
    g2 = g + (grey - g) * amount
    b2 = b + (grey - b) * amount
    return f"rgba({int(r2 * 255)}, {int(g2 * 255)}, {int(b2 * 255)}, {a:.3f})"


def _status_color(status: str) -> str:
    """Stable colour for a status string. Known ones get a hand-picked hue;
    unknowns fall through to tab20 via a hash so they stay distinguishable."""
    if status in STATUS_COLOR_MAP:
        return STATUS_COLOR_MAP[status]
    import matplotlib.pyplot as _plt
    cmap = _plt.colormaps["tab20"]
    idx = (hash(status) % 20) / 19.0
    return _rgba_to_css(cmap(idx))


def _empty_picker_df() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="object") for c in PICKER_COLUMNS})
    df["Select"] = df["Select"].astype(bool)
    return df


def _mpl_colorscale(cmap, n: int = 16) -> list[list]:
    """Sample a matplotlib colormap into a plotly [[frac, css], …] colorscale."""
    return [[i / (n - 1), _rgba_to_css(cmap(i / (n - 1)))] for i in range(n)]


def _parse_limit(value: str | None) -> float | None:
    """Parse an axis-limit text field. Blank / unparseable → None (auto)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _axis_label(axis: str) -> str:
    if axis == "time":
        return "Time (h)"
    return _AXIS_TABLE[axis][1]


def _axis_resets(axis: str) -> bool:
    if axis == "time":
        return False
    return _AXIS_TABLE[axis][2]


def _axis_series(df: pd.DataFrame, axis: str) -> pd.Series:
    """Return the series for the named axis from a navani DataFrame."""
    if axis == "time":
        return cumulative_time_hours(df)
    col = _AXIS_TABLE[axis][0]
    return df[col]


def _axis_col_in(df: pd.DataFrame, axis: str) -> tuple[pd.DataFrame, str]:
    """Return ``(df_with_axis_as_column, column_name)``.

    For ``axis="time"`` writes a ``_time_h`` column derived via
    ``cumulative_time_hours``; for the others returns the existing column
    name. The returned DataFrame may be a shallow copy when a column has
    to be added.
    """
    if axis == "time":
        if "_time_h" not in df.columns:
            df = df.copy()
            df["_time_h"] = cumulative_time_hours(df).to_numpy()
        return df, "_time_h"
    return df, _AXIS_TABLE[axis][0]
