"""Constants, lookup tables and the :class:`PlotStyle` dataclass for the GUI.

No Streamlit, no rendering — just data. ``load_dotenv()`` runs here so the
``.env`` credential snapshot below is taken before any in-app Connect can
mutate ``os.environ``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Pre-fills the GUI Connect modal when nothing else is known. Deliberately
# empty: a datalab instance is site-specific, so a shipped default would
# point every install at one team's server. Set DATALAB_URL to pre-fill.
DEFAULT_URL = ""

# Snapshot the .env / shell credentials at import time, BEFORE any in-app
# Connect can mutate os.environ. The datalab_api client only reads the key
# from os.environ (process-global), so without this snapshot a manual
# Connect to instance B would leave key B in os.environ, and a later
# auto-connect (e.g. after reopening the browser → fresh session) would
# pair the .env URL (instance A) with the stale key B — "connected" but
# broken. Auto-connect uses ONLY this immutable snapshot.
# .strip() guards against a trailing newline / whitespace in the .env file
# (or a stray space from a copy-paste): the datalab client only strips
# surrounding quotes, not whitespace, so an un-stripped key is sent as
# `DATALAB-API-KEY: <key>\n` and silently rejected by the server.
_ENV_URL = os.environ.get("DATALAB_URL", "").strip()
_ENV_KEY = os.environ.get("DATALAB_API_KEY", "").strip()

PICKER_COLUMNS = (
    "Select", "item_id", "name",
    "positive_electrode", "negative_electrode", "electrolyte",
    "cathode_mass_mg",
    "label", "group", "color",
)

PICKER_KEY_BASE = "picker_editor"

# Common cycler step-type / state values mapped to colours. Anything not in
# the map falls back to a deterministic hash-based tab20 colour, so unknown
# statuses still render distinguishably.
STATUS_COLOR_MAP: dict[str, str] = {
    "CC_Chg":    "#e63946",
    "CV_Chg":    "#f1a208",
    "CCCV_Chg":  "#d62728",
    "CC Chg":    "#e63946",
    "CC_DChg":   "#1f77b4",
    "CV_DChg":   "#56b4e9",
    "CCCV_DChg": "#1a5fb4",
    "CC DChg":   "#1f77b4",
    "Rest":      "#9aa0a6",
    "rest":      "#9aa0a6",
    "R":         "#9aa0a6",
    "Pause":     "#cfd2d6",
    "0":         "#e63946",   # navani: charge
    "1":         "#1f77b4",   # navani: discharge
    "unknown":   "#cccccc",
}

# ---------------------------------------------------------------------------
# Axis machinery for the generic XY mode.
#
# Each axis option resolves to (column name, axis label, resets_per_half_cycle?).
# The `resets_per_half_cycle` flag is True for any column whose value is reset
# at half-cycle boundaries (currently just `Capacity` in navani's output);
# when EITHER axis has that flag set, the plot needs `split_half_cycles` to
# avoid drawing fold-back connectors.
# ---------------------------------------------------------------------------

AXIS_OPTIONS = ("time", "voltage", "capacity", "current")
Y2_OPTIONS = ("none", "time", "voltage", "capacity", "current")

# Map axis key → (column name in raw df, label, resets_per_half_cycle).
# `time` is special — it uses cumulative_time_hours instead of a raw column.
_AXIS_TABLE: dict[str, tuple[str, str, bool]] = {
    "voltage":  ("Voltage",  "Voltage (V)",     False),
    "capacity": ("Capacity", "Capacity (mAh)",  True),
    "current":  ("Current",  "Current (mA)",    False),
}

# --- Presets ---------------------------------------------------------------
# A single-select segmented control drives the plot mode + axes.

PRESET_OPTIONS = (
    "V vs t", "V vs Q", "I vs t", "Q vs t", "V & I vs t",
    "dQ/dV", "Cycle Life", "Custom",
)

# Each entry: (mode, x_axis, y_axis, y2_axis). None means "leave current value".
PRESET_MAP: dict[str, tuple[str, str | None, str | None, str | None]] = {
    "V vs t":     ("xy",                "time", "voltage",  "none"),
    "V vs Q":     ("voltage_capacity",  None,   None,       None),
    "I vs t":     ("xy",                "time", "current",  "none"),
    "Q vs t":     ("xy",                "time", "capacity", "none"),
    "V & I vs t": ("xy",                "time", "voltage",  "current"),
    "dQ/dV":      ("dqdv",              None,   None,       None),
    "Cycle Life": ("summary",           None,   None,       None),
    # Custom: switch to xy but don't overwrite the user's current axes.
    "Custom":     ("xy",                None,   None,       None),
}

# Default value for every plot-option widget. Single source of truth for
# both the first-render seeding and the Reset button.
PLOT_OPTION_DEFAULTS: dict[str, object] = {
    "ui_preset": "V vs t",
    "ui_mode": "xy",
    "ui_x_axis": "time",
    "ui_y_axis": "voltage",
    "ui_y2_axis": "none",
    "ui_title": "",
    "ui_color_by_status": True,
    "ui_plot_width": 90,
    "ui_plot_height": 520,
    "ui_width_scale": 2.0,
    "ui_legend_mode": "below",
    "ui_font_size": 13,
    "ui_colorbar": False,
    "ui_border": True,
    "ui_grid_x": True,
    "ui_grid_y": True,
    "ui_marker_mode": "Lines",
    "ui_marker_size": 6,
    "ui_xmin": "", "ui_xmax": "", "ui_ymin": "", "ui_ymax": "",
    "ui_y2min": "", "ui_y2max": "",
    "ui_specific_capacity": False,
}


@dataclass(frozen=True)
class PlotStyle:
    """User-tunable plot appearance (set in the Plot options expander).

    Frozen so it's hashable — it goes straight into the live-refresh
    change-detection signature.

    Axis limits are ``None`` for auto-range; a manual range is applied
    only when *both* bounds of an axis are given.
    """

    border: bool = True            # box outline around the plot area
    grid_x: bool = True            # vertical gridlines (x-axis grid)
    grid_y: bool = True            # horizontal gridlines (y-axis grid)
    legend_mode: str = "below"     # "below" | "overlaid" | "none"
    font_size: int = 13
    colorbar: bool = False         # V-vs-Q: per-cell cycle-number colorbar
    marker_mode: str = "lines"     # "lines" | "lines+markers" | "markers"
    marker_size: float = 6.0       # dot diameter in px (ignored when marker_mode="lines")
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    y2_min: float | None = None
    y2_max: float | None = None
