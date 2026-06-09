"""Dash GUI entry point for datalab-plot. Launched via ``datalab-plot gui-dash``.

Parallel to the Streamlit GUI in :mod:`datalab_plot.gui`. The two share the
same pure layer (parsers, series, plotting builders, ``PlotStyle``); only the
rendering shell differs.

The layout post-iteration-1 is a single column (no left sidebar): the
Connect form lives in a navbar dropdown + modal, freeing the main content
to take full width.
"""
from __future__ import annotations

import logging
import os
import secrets
import webbrowser
from threading import Timer

import dash
import dash_bootstrap_components as dbc
from dash import dcc, html

from datalab_plot.gui_dash import (
    connection,
    export,
    options,
    picker,
    plotting_panel,
    search,
    staging,
)

logger = logging.getLogger(__name__)


# Global CSS — keeps the page from horizontally jittering as content height
# changes (scrollbar always reserved), and tightens the Bootstrap defaults
# so this dense data-analysis UI doesn't feel like a marketing page.
_GLOBAL_CSS = """
/* ============================================================
   Design system — single source of truth for the Dash GUI.
   Brand palette:
     #000072 navy  — primary interactive
     #E6EAEE grey  — soft brand surface (navbar + apply toolbar)
     #00FFBA mint  — connected-status dot only
     #FAB400 gold  — warning tint
   ============================================================ */
:root {
    --bs-primary: #000072;
    --bs-primary-rgb: 0, 0, 114;
}

html { overflow-y: scroll; }
body { padding-bottom: 2rem; }

/* --- Navbar: brand grey surface ---------------------------- */
.navbar {
    padding-top: 0.4rem;
    padding-bottom: 0.4rem;
    background-color: #E6EAEE !important;
}
.navbar-brand { color: #212529 !important; }

/* --- Section dividers between top-level panels ------------- */
.ui-section {
    padding-bottom: 0.75rem;
    margin-bottom: 0.75rem;
    border-bottom: 1px solid #f1f3f5;
}
.ui-section:last-child {
    border-bottom: none;
    margin-bottom: 0;
}

/* --- Primary buttons render in brand navy ------------------ */
.btn-primary {
    --bs-btn-bg: #000072;
    --bs-btn-border-color: #000072;
    --bs-btn-hover-bg: #1a1a91;
    --bs-btn-hover-border-color: #1a1a91;
    --bs-btn-active-bg: #1a1a91;
    --bs-btn-active-border-color: #1a1a91;
    --bs-btn-disabled-bg: #000072;
    --bs-btn-disabled-border-color: #000072;
}
.btn-outline-primary {
    --bs-btn-color: #000072;
    --bs-btn-border-color: #000072;
    --bs-btn-hover-bg: #000072;
    --bs-btn-hover-border-color: #000072;
    --bs-btn-active-bg: #000072;
    --bs-btn-active-border-color: #000072;
    --bs-btn-disabled-color: #000072;
    --bs-btn-disabled-border-color: #000072;
}

/* --- Warning alerts in brand gold tint --------------------- */
.alert-warning {
    --bs-alert-bg: rgba(250, 180, 0, 0.14);
    --bs-alert-border-color: rgba(250, 180, 0, 0.45);
    --bs-alert-color: #5a4900;
}

/* --- Misc Bootstrap density tightening --------------------- */
.dropdown-menu { font-size: 0.875rem; }
.ag-theme-alpine { --ag-font-size: 13px; --ag-grid-size: 5px; }

/* --- Plot-mode selector: horizontal-scroll wrapper only -----
   Buttons are real <button> children of a dbc.ButtonGroup, so Bootstrap's
   stock .btn-group CSS handles the shared-edge connection natively. We
   only add horizontal scrolling for narrow viewports. */
.preset-scroller {
    overflow-x: auto;
    padding-bottom: 2px;
}
.preset-scroller .btn-group { flex-wrap: nowrap; }

/* --- Picker grid wrapper (collapsible + expandable) -------- */
.picker-grid-resizer {
    overflow: hidden;
    height: 400px;
    border: 1px solid #dee2e6;
    border-radius: 0.375rem;
    background: white;
    transition: height 0.15s ease;
}
.picker-grid-resizer.expanded { height: 80vh; }
.picker-grid-resizer .ag-theme-alpine { border: none; }

/* --- Apply-to-selection toolbar (brand soft surface) ------- */
.apply-toolbar {
    background: #E6EAEE;
    border: 1px solid #dee2e6;
    border-radius: 0.375rem;
    padding: 0.5rem 0.75rem;
}
.apply-toolbar .form-label {
    margin-bottom: 0;
    font-size: 0.8125rem;
    color: #495057;
}
"""


def _make_app() -> dash.Dash:
    """Build the Dash app, register all panel callbacks, and return it."""
    app = dash.Dash(
        __name__,
        title="datalab-plot",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    app.server.secret_key = secrets.token_hex(32)

    # Inject the global CSS into the app's HTML index so it loads before
    # any callback fires.
    app.index_string = app.index_string.replace(
        "{%css%}", "{%css%}\n<style>" + _GLOBAL_CSS + "</style>",
    )
    # Replace Dash's default {%favicon%} placeholder (which only auto-fills
    # for favicon.ico) with an explicit SVG link tag, pointing at the brand
    # mark in gui_dash/assets/.
    app.index_string = app.index_string.replace(
        "{%favicon%}",
        '<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg">',
    )

    app.layout = dbc.Container(
        [
            # Stores hold serializable UI state that other callbacks observe.
            # Server-side state (client, parsed DataFrames, last_fig) lives in
            # gui_dash.state.get_state() instead.
            dcc.Store(id="connection-version", data=0),
            dcc.Store(id="search-version", data=0),
            dcc.Store(id="staging-version", data=0),
            dcc.Store(id="save-version", data=0),
            dcc.Store(id="picker-payload", data={}),
            dcc.Store(id="plot-version", data=0),
            dcc.Location(id="url", refresh=False),
            dbc.Navbar(
                dbc.Container(
                    [
                        dbc.NavbarBrand("datalab-plot", className="fw-bold"),
                        html.Div(connection.layout(), className="ms-auto"),
                    ],
                    fluid=True,
                ),
                color="light",
                className="mb-2 border-bottom",
            ),
            html.Div(id="connect-prompt"),
            html.Div(
                [
                    html.Div(search.layout(), className="ui-section"),
                    html.Div(picker.layout(), className="ui-section"),
                    html.Div(staging.layout(), className="ui-section"),
                    html.Div(options.layout(), className="ui-section"),
                    html.Div(plotting_panel.layout(), className="ui-section"),
                    html.Div(export.layout(), className="ui-section"),
                ],
                id="main-content",
            ),
        ],
        fluid=True,
        className="px-3",
    )

    connection.register_callbacks(app)
    search.register_callbacks(app)
    picker.register_callbacks(app)
    staging.register_callbacks(app)
    options.register_callbacks(app)
    plotting_panel.register_callbacks(app)
    export.register_callbacks(app)

    # Toggle main-content visibility based on connection state.
    from dash import Input, Output

    from datalab_plot.gui_dash.state import get_state

    @app.callback(
        Output("main-content", "style"),
        Output("connect-prompt", "children"),
        Input("connection-version", "data"),
    )
    def _toggle_main(_version):  # type: ignore[no-untyped-def]
        state = get_state()
        if state.get("client") is None:
            return (
                {"display": "none"},
                dbc.Alert(
                    "Connect to a datalab instance from the top-right Connect "
                    "button to begin.",
                    color="info",
                    className="mt-2",
                ),
            )
        return {}, ""

    return app


def main(port: int = 8050, open_browser: bool = True) -> None:
    """Start the Dash server.

    ``port`` may be reassigned by the caller (``cli._find_free_port``) when
    busy. ``open_browser`` opens a browser tab once the server is listening.
    """
    import matplotlib

    matplotlib.use("Agg")

    app = _make_app()
    if open_browser and not os.environ.get("DATALAB_PLOT_NO_BROWSER"):
        Timer(1.0, lambda: webbrowser.open_new_tab(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
