"""Streamlit GUI entry point for datalab-plot. Launched via ``datalab-plot gui``.

Streamlit runs this file as a top-level script, so every datalab_plot import
is absolute. ``matplotlib.use("Agg")`` runs before any module that imports
``matplotlib.pyplot`` so the headless backend is locked in.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import streamlit as st  # noqa: E402

from datalab_plot.gui.connection import _sidebar_connection  # noqa: E402
from datalab_plot.gui.export import _png_export_section  # noqa: E402
from datalab_plot.gui.options_panel import _plot_bar  # noqa: E402
from datalab_plot.gui.picker_panel import _picker_table, _selected_payload  # noqa: E402
from datalab_plot.gui.plotting import _render_cached_figure, _render_plot  # noqa: E402
from datalab_plot.gui.search_panel import (  # noqa: E402
    _autopopulate_recent,
    _search_section,
)

# Favicon — a small-size-optimised variant of the brand logo (assets/logo.svg).
# Streamlit's set_page_config(page_icon=...) detects raw SVG strings via a
# regex on `<svg `. We inline it so the icon ships with the package and loads
# regardless of install path.
#
# Differences from the full brand mark: no gridlines, no axes, thicker traces
# (22 vs 9 in a 256-px viewBox → ~1.4 px stroke at 16x16, vs ~0.6 px before),
# larger endpoint dots with a dark-blue contrast ring, chart fills more of the
# tile. The full detail-rich logo still lives at assets/logo.svg for README /
# brand use at larger sizes.
_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect x="8" y="8" width="240" height="240" rx="54" fill="#000072"/>
  <path d="M 48 184 C 92 178 142 168 188 168"
    fill="none" stroke="#0083FF" stroke-width="22" stroke-linecap="round"/>
  <path d="M 48 180 C 92 112 142 78 188 72"
    fill="none" stroke="#00FFBA" stroke-width="22" stroke-linecap="round"/>
  <circle cx="188" cy="72" r="28" fill="#000072"/>
  <circle cx="188" cy="72" r="18" fill="#FAB400"/>
  <circle cx="188" cy="168" r="28" fill="#000072"/>
  <circle cx="188" cy="168" r="18" fill="#FAB400"/>
</svg>"""

_GLOBAL_CSS = """
<style>
/* Prevent button labels and widget labels (incl. st.toggle) from wrapping
   into one-character columns on narrow windows. */
.stButton button p,
.stButton button div,
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] label {
    white-space: nowrap !important;
}
</style>
"""


def main() -> None:
    st.set_page_config(page_title="datalab-plot", page_icon=_FAVICON_SVG, layout="wide")
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)
    st.title("datalab-plot")

    client = _sidebar_connection()
    if client is None:
        st.info("Connect to a datalab instance from the sidebar to begin.")
        return

    _autopopulate_recent(client)
    _search_section(client)
    picker_df = _picker_table()
    (
        mode, x_axis, y_axis, y2_axis, cycle, title,
        refresh_click, live, color_by_status,
        width_frac, width_scale, height_px, style,
    ) = _plot_bar()

    payload = _selected_payload(picker_df)

    # Detect whether the live-mode plot inputs changed since the last render —
    # if not, skip the rebuild even with Auto on. This keeps checkbox toggles
    # snappy when only e.g. the title or width slider moves. `style` is a
    # frozen dataclass, so it drops straight into the signature tuple.
    plot_signature = (
        tuple(sorted((k, v.get("item_id"), v.get("group"), v.get("color"))
                     for k, v in payload.items())),
        mode, x_axis, y_axis, y2_axis, cycle, title,
        color_by_status, width_frac, width_scale, height_px, style,
    )
    selection_changed = (
        plot_signature != st.session_state.get("last_plot_signature")
    )

    should_render = (
        refresh_click
        or (live and selection_changed and payload)
    )

    if should_render:
        _render_plot(
            client, payload, mode, cycle, title, width_frac, height_px,
            x_axis=x_axis, y_axis=y_axis, y2_axis=y2_axis,
            color_by_status=color_by_status, width_scale=width_scale, style=style,
            force_refresh=refresh_click,
        )
        st.session_state["last_plot_signature"] = plot_signature

    # Always re-display the cached figure (kept stable by key="main_plot"),
    # so checkbox toggles when Auto-refresh is off don't blank the plot.
    if "last_fig" in st.session_state:
        _render_cached_figure()
        _png_export_section(style)
    elif payload:
        st.caption("Tick rows and pick a plot type — the figure renders automatically.")
    else:
        st.caption("Tick rows in the picker, then click **Plot**.")


if __name__ == "__main__":
    main()
