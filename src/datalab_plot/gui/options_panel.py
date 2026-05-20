"""Plot-options bar: the preset control, the Plot options expander, and the
Refresh / Auto action row.
"""
from __future__ import annotations

import streamlit as st

from datalab_plot.gui.constants import (
    AXIS_OPTIONS,
    PLOT_OPTION_DEFAULTS,
    PRESET_MAP,
    PRESET_OPTIONS,
    Y2_OPTIONS,
    PlotStyle,
)
from datalab_plot.gui.helpers import _parse_limit


def _apply_preset(preset: str) -> None:
    mode, x, y, y2 = PRESET_MAP[preset]
    st.session_state["ui_mode"] = mode
    if x is not None:
        st.session_state["ui_x_axis"] = x
    if y is not None:
        st.session_state["ui_y_axis"] = y
    if y2 is not None:
        st.session_state["ui_y2_axis"] = y2


def _on_preset_change() -> None:
    preset = st.session_state.get("ui_preset")
    if preset in PRESET_MAP:
        _apply_preset(preset)


def _on_customize_edit() -> None:
    """User touched a widget inside the Customize expander → fall out of any
    named preset and switch to Custom."""
    st.session_state["ui_preset"] = "Custom"


def _cb_reset_options() -> None:
    """Reset every plot-option widget to its default. Runs as a button
    on_click callback, so it executes before the widgets re-instantiate —
    writing their session_state keys here is allowed."""
    for k, v in PLOT_OPTION_DEFAULTS.items():
        st.session_state[k] = v


def _style_controls() -> tuple[int, int, float, str, int, bool, bool, bool, bool]:
    """Render the Layout & styling rows. Returns
    ``(width_pct, height_px, width_scale, legend_mode, font_size, colorbar,
    border, grid_x, grid_y)``."""
    st.divider()
    st.caption("Layout & styling")
    lcols = st.columns(3)
    width_pct = lcols[0].slider(
        "Plot width", min_value=40, max_value=100,
        step=5, format="%d%%", key="ui_plot_width",
    )
    height_px = lcols[1].slider(
        "Plot height (px)", min_value=320, max_value=900,
        step=20, key="ui_plot_height",
    )
    width_scale = lcols[2].slider(
        "Trace width", min_value=0.5, max_value=5.0,
        step=0.25, key="ui_width_scale",
        help="Multiplies every line width. Useful for screenshots / projectors.",
    )

    scols = st.columns(3)
    legend_mode = scols[0].selectbox(
        "Legend",
        options=["below", "overlaid", "none"],
        key="ui_legend_mode",
        help="below = horizontal under the plot · overlaid = inset top-right · none = hidden",
    )
    font_size = scols[1].slider(
        "Text size", min_value=8, max_value=28,
        step=1, key="ui_font_size",
    )
    colorbar = scols[2].toggle(
        "Cycle colorbar",
        key="ui_colorbar",
        help="Voltage-vs-capacity only: add a per-cell cycle-number colorbar.",
    )

    tcols = st.columns(3)
    border = tcols[0].toggle(
        "Outer border",
        key="ui_border",
        help="Box outline around the plot area.",
    )
    grid_x = tcols[1].toggle("Vertical gridlines", key="ui_grid_x")
    grid_y = tcols[2].toggle("Horizontal gridlines", key="ui_grid_y")
    return (
        width_pct, height_px, width_scale, legend_mode, font_size,
        colorbar, border, grid_x, grid_y,
    )


def _axis_limit_inputs(has_y2: bool) -> tuple[str, str, str, str, str, str]:
    """Render the manual axis-limit text fields. Returns the six raw strings
    ``(x_min, x_max, y_min, y_max, y2_min, y2_max)`` — blank means auto."""
    st.caption("Axis limits — leave blank for auto:")
    acols = st.columns(6 if has_y2 else 4)
    x_min = acols[0].text_input("x min", key="ui_xmin")
    x_max = acols[1].text_input("x max", key="ui_xmax")
    y_min = acols[2].text_input("y min", key="ui_ymin")
    y_max = acols[3].text_input("y max", key="ui_ymax")
    if has_y2:
        y2_min = acols[4].text_input("y₂ min", key="ui_y2min")
        y2_max = acols[5].text_input("y₂ max", key="ui_y2max")
    else:
        y2_min = y2_max = ""
    return x_min, x_max, y_min, y_max, y2_min, y2_max


def _plot_bar() -> tuple[
    str, str, str, str, int | None, str, bool, bool, bool, float, float, int, PlotStyle
]:
    # Seed defaults the first time these widgets render. PLOT_OPTION_DEFAULTS
    # is the single source of truth, shared with the Reset button.
    for _k, _v in PLOT_OPTION_DEFAULTS.items():
        st.session_state.setdefault(_k, _v)

    # Preset segmented control — single visible row, single source of truth
    # for the named-view selection.
    st.segmented_control(
        "Plot type",
        options=list(PRESET_OPTIONS),
        key="ui_preset",
        on_change=_on_preset_change,
        label_visibility="collapsed",
    )

    mode = st.session_state["ui_mode"]
    is_xy = mode == "xy"

    # Cycle stays inline directly under the preset row, but only when the
    # active mode actually uses it.
    cycle: int | None = None
    if mode == "dqdv":
        cycle = int(
            st.number_input(
                "Cycle", min_value=1, step=1,
                value=st.session_state.get("ui_cycle", 1),
                key="ui_cycle",
                on_change=_on_customize_edit,
            )
        )

    # All plot options live in one expander — axes/title, figure size,
    # styling, and manual limits — closed by default since the preset row
    # covers the common cases.
    with st.expander("Plot options", expanded=False):
        st.caption("Axes & title")
        cols = st.columns([1.2, 1, 1, 1, 3])
        cols[0].selectbox(
            "Mode",
            ["xy", "voltage_capacity", "dqdv", "summary"],
            key="ui_mode",
            on_change=_on_customize_edit,
        )
        cols[1].selectbox(
            "X", AXIS_OPTIONS,
            key="ui_x_axis",
            disabled=not is_xy,
            on_change=_on_customize_edit,
        )
        cols[2].selectbox(
            "Y (left)", AXIS_OPTIONS,
            key="ui_y_axis",
            disabled=not is_xy,
            on_change=_on_customize_edit,
        )
        cols[3].selectbox(
            "Y₂ (right)", Y2_OPTIONS,
            key="ui_y2_axis",
            disabled=not is_xy,
            help="Pick 'none' for a single Y axis; pick any column to overlay it on the right.",
            on_change=_on_customize_edit,
        )
        title = cols[4].text_input("Title (optional)", key="ui_title")
        st.toggle(
            "Colour traces by cycler step (CC_Chg / CV_Chg / Rest …)",
            key="ui_color_by_status",
            disabled=not is_xy,
            help=(
                "When on, each trace is split into segments coloured by the file's "
                "Status / state column. Disables per-cell colouring and the right "
                "Y-axis."
            ),
        )

        (
            width_pct, height_px, width_scale, legend_mode, font_size,
            colorbar, border, grid_x, grid_y,
        ) = _style_controls()

        # Manual axis limits — blank = auto. A limit applies only when both
        # bounds of an axis are filled in.
        has_y2 = st.session_state.get("ui_y2_axis", "none") != "none"
        x_min, x_max, y_min, y_max, y2_min, y2_max = _axis_limit_inputs(has_y2)

        st.divider()
        st.button(
            "Reset all options to defaults",
            on_click=_cb_reset_options,
            help="Restore every option above (and the preset) to its default.",
        )

    style = PlotStyle(
        border=border,
        grid_x=grid_x,
        grid_y=grid_y,
        legend_mode=legend_mode,
        font_size=int(font_size),
        colorbar=colorbar,
        x_min=_parse_limit(x_min),
        x_max=_parse_limit(x_max),
        y_min=_parse_limit(y_min),
        y_max=_parse_limit(y_max),
        y2_min=_parse_limit(y2_min),
        y2_max=_parse_limit(y2_max),
    )

    # Compact action row. Columns are wide enough that the labels can't
    # collapse to per-letter wrapping on narrow windows. The `Auto` toggle
    # uses a shortened label (help tooltip carries the full description).
    action = st.columns([1, 1, 2], vertical_alignment="center")
    refresh_click = action[0].button(
        "Refresh",
        help="Purge local cache for selected items and re-fetch from the server.",
        use_container_width=True,
    )
    live = action[1].toggle(
        "Auto",
        value=st.session_state.get("ui_live", True),
        help="Auto-refresh: re-render the plot on every selection / preset change.",
        key="ui_live",
    )

    x_axis = st.session_state["ui_x_axis"]
    y_axis = st.session_state["ui_y_axis"]
    y2_axis = st.session_state["ui_y2_axis"]
    color_by_status = bool(st.session_state.get("ui_color_by_status", False))
    return (
        mode, x_axis, y_axis, y2_axis, cycle, title,
        refresh_click, live, color_by_status,
        width_pct / 100.0, float(width_scale), height_px, style,
    )
