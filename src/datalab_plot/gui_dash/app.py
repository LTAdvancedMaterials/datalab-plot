"""Dash GUI entry point for datalab-plot. Launched via ``datalab-plot gui``.

Sole web front-end for the project. Shares the pure data layer (parsers,
series, plot builders, ``PlotStyle``) with the Python API and CLI; this
module is the rendering shell.

Layout: two columns under a navbar. The left column owns search, picker,
staging, plot-options, and export; the right column owns the preset
selector + the plot. A draggable vertical divider sets the split.
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
   See CLAUDE.md > "Typographic style guide" for the full rules.

   Brand palette:
     #000072 navy  — primary interactive (buttons, segmented active)
     #E6EAEE grey  — soft brand surface (navbar + apply toolbar)
     #00FFBA mint  — connected-status dot only
     #FAB400 gold  — warning-alert tint
   ============================================================ */

/* --- Tokens ------------------------------------------------ */
:root {
    /* Bootstrap primary -> brand navy */
    --bs-primary: #000072;
    --bs-primary-rgb: 0, 0, 114;

    /* Type scale (5 sizes) */
    --text-lg:   1.0625rem;   /* 17px — navbar brand only */
    --text-base: 1rem;        /* 16px — body, buttons, inputs */
    --text-md:   0.9375rem;   /* 15px — section title (above body) */
    --text-sm:   0.8125rem;   /* 13px — field labels, captions, meta */
    --text-xs:   0.6875rem;   /* 11px — subsection labels */

    /* Text colors */
    --text-body:    #212529;
    --text-muted:   #6c757d;
    --text-success: #198754;
    --text-danger:  #dc3545;

    /* Spacing scale (4 steps) */
    --space-xs: 0.25rem;
    --space-sm: 0.5rem;
    --space-md: 0.75rem;
    --space-lg: 1rem;
}

html { overflow-y: scroll; }
body { padding-bottom: 2rem; }

/* Tame <strong> to semibold (Bootstrap default is 700; design uses 600). */
strong { font-weight: 600; }

/* --- Semantic typography classes --------------------------- */
/* Wordmark: only place text-lg is used. */
.navbar-brand {
    font-size: var(--text-lg);
    font-weight: 600;
    color: var(--text-body) !important;
}

/* Section title — applied to the chevron collapse-button label. */
.ui-section-title {
    font-size: var(--text-md);
    font-weight: 600;
    color: var(--text-body);
    line-height: 1.4;
}

/* Decorative status dot (e.g. navbar connection ●). Sized relative to
   surrounding text so it scales with the line, not the type scale. */
.connection-status-dot {
    color: #00FFBA;
    margin-right: 0.4rem;
    font-size: 0.7em;
    vertical-align: middle;
}

/* Subsection label — small uppercase muted. SOLE muted-semibold use,
   justified by uppercase + letter-spacing reading as a delimiter. */
.ui-subsection-label {
    display: block;
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: var(--space-sm);
}

/* Field label (above or beside an input). */
.ui-field-label {
    font-size: var(--text-sm);
    font-weight: 400;
    color: var(--text-muted);
    margin-bottom: var(--space-xs);
}

/* Meta / status line ("3 selected of 30", "5 staged"). */
.ui-meta {
    font-size: var(--text-sm);
    color: var(--text-muted);
}
.ui-meta strong {
    font-weight: 600;
    color: var(--text-body);
}

/* Helper caption / hint. */
.ui-caption {
    font-size: var(--text-sm);
    color: var(--text-muted);
}

/* Feedback messages — always sm; combine -success / -danger / (none for muted). */
.ui-feedback         { font-size: var(--text-sm); }
.ui-feedback-success { color: var(--text-success); }
.ui-feedback-danger  { color: var(--text-danger); }

/* --- Navbar: brand grey surface ---------------------------- */
.navbar {
    padding-top: 0.4rem;
    padding-bottom: 0.4rem;
    background-color: #E6EAEE !important;
}

/* --- Section dividers between top-level panels ------------- */
.ui-section {
    padding-bottom: var(--space-md);
    margin-bottom: var(--space-md);
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

/* --- Alerts: unified padding, size, color tokens ----------- */
.alert {
    padding: var(--space-sm) var(--space-md);
    font-size: var(--text-sm);
    margin-bottom: var(--space-sm);
}
.alert-warning {
    --bs-alert-bg: rgba(250, 180, 0, 0.14);
    --bs-alert-border-color: rgba(250, 180, 0, 0.45);
    --bs-alert-color: #5a4900;
}

/* --- Misc density --------------------------------------------- */
.dropdown-menu { font-size: var(--text-sm); }
.ag-theme-alpine,
.ag-theme-alpine-dark { --ag-font-size: 13px; --ag-grid-size: 5px; }

/* Staged-row tint + editable-cell highlight colours read by AG Grid
   inline styles (cellStyle in staging.py, getRowStyle in picker.py).
   One value per theme; the CSS vars resolve at paint time so theme
   switches re-tint without a grid re-render. */
:root {
    --ag-row-staged-bg: #e8f1ff;
    --ag-editable-cell-bg: #fff8e1;
}
[data-bs-theme="dark"] {
    --ag-row-staged-bg: #1e3a5c;
    --ag-editable-cell-bg: #2d2a1a;
}

/* --- Plot-mode selector: horizontal-scroll wrapper, centered ------
   Buttons are real <button> children of a dbc.ButtonGroup, so Bootstrap's
   stock .btn-group CSS handles the shared-edge connection natively. We
   add horizontal scrolling for narrow viewports and center the inline
   btn-group child via text-align. */
.preset-scroller {
    overflow-x: auto;
    padding-bottom: 2px;
    text-align: center;
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
    padding: var(--space-sm) var(--space-md);
}

/* --- Two-column shell with draggable divider ----------------
   CSS Grid with three tracks: left content | divider | right content.
   The left track's width comes from a CSS variable that the divider's
   clientside mousemove handler updates (clamped 25%..85%). Stacks to a
   single column below 992 px; divider hidden. */
.ui-two-col {
    display: grid;
    grid-template-columns: var(--left-col-width, 60%) 6px 1fr;
    gap: 0;
    align-items: start;
}
.ui-col-left  { padding-right: 0.75rem; min-width: 0; }
.ui-col-right {
    padding-left: 0.75rem;
    min-width: 0;
    /* Lock the right column to the top of the viewport — only the left
       column scrolls with the document. align-self: start opts out of
       the grid's `align-items: start` (no stretch) and is required for
       sticky to work inside a grid child. */
    position: sticky;
    top: 0.5rem;
    align-self: start;
    max-height: calc(100vh - 1rem);
    overflow-y: auto;
}
.ui-col-divider {
    /* Counter the parent's `align-items: start` — otherwise an empty
       div collapses to 0 height and is uninteractable. */
    align-self: stretch;
    background: #dee2e6;
    cursor: ew-resize;
    width: 6px;
    transition: background 0.15s;
}
.ui-col-divider:hover,
.ui-col-divider.dragging { background: #adb5bd; }

/* Plot wrapper: carries a CSS variable --plot-height that the horizontal
   divider updates on drag. .ui-plot-graph reads it. */
.ui-plot-resizer {
    --plot-height: calc(100vh - 11rem);
}
.ui-plot-graph {
    width: 100% !important;
    height: var(--plot-height) !important;
    min-height: 200px !important;
}
.ui-plot-h-divider {
    height: 6px;
    background: #dee2e6;
    cursor: ns-resize;
    transition: background 0.15s;
    margin-top: 0.25rem;
    border-radius: 0.125rem;
}
.ui-plot-h-divider:hover,
.ui-plot-h-divider.dragging { background: #adb5bd; }

/* Single-column override — applied either by viewport (narrow) or by
   the navbar 1 / 2 column-mode toggle (via .force-single-col). When the
   layout collapses to one column, the right column's sticky positioning
   is reset to static so it flows naturally below the left column. */
@media (max-width: 991.98px) {
    .ui-two-col { grid-template-columns: 1fr; }
    .ui-col-divider { display: none; }
    .ui-col-left  { padding-right: 0; }
    .ui-col-right {
        padding-left: 0;
        position: static;
        max-height: none;
        overflow-y: visible;
    }
    .ui-plot-resizer { --plot-height: 60vh; }
}
.ui-two-col.force-single-col { grid-template-columns: 1fr; }
.ui-two-col.force-single-col .ui-col-divider { display: none; }
.ui-two-col.force-single-col .ui-col-left  { padding-right: 0; }
.ui-two-col.force-single-col .ui-col-right {
    padding-left: 0;
    position: static;
    max-height: none;
    overflow-y: visible;
}
.ui-two-col.force-single-col .ui-plot-resizer { --plot-height: 60vh; }

/* dbc.Switch inner labels — match the .ui-field-label style instead of
   the default body size. Applies to every Switch in the app. */
.form-switch .form-check-label {
    font-size: var(--text-sm);
    color: var(--text-muted);
    font-weight: 400;
}

/* --- Dark mode chrome overrides ---------------------------------
   Triggered by `data-bs-theme="dark"` on <html>. The Plotly figure
   template flips via the theme Store → _aggregate → plot callback
   pipeline (plotly_dark when dark). AG Grid swaps to alpine-dark via
   the theme-toggle clientside callback writing className. */
[data-bs-theme="dark"] body {
    background-color: #1a1d20;
    color: #e9ecef;
}
[data-bs-theme="dark"] .navbar         { background-color: #2b2f33 !important; }
[data-bs-theme="dark"] .navbar-brand   { color: #e9ecef !important; }
[data-bs-theme="dark"] .ui-section     { border-bottom-color: #2b2f33; }
[data-bs-theme="dark"] .ui-section-title { color: #e9ecef; }
[data-bs-theme="dark"] .ui-meta strong { color: #e9ecef; }
[data-bs-theme="dark"] .ui-subsection-label,
[data-bs-theme="dark"] .ui-field-label,
[data-bs-theme="dark"] .ui-meta,
[data-bs-theme="dark"] .ui-caption,
[data-bs-theme="dark"] .form-switch .form-check-label,
[data-bs-theme="dark"] .ui-feedback         { color: #adb5bd; }
[data-bs-theme="dark"] .ui-feedback-success { color: #75b798; }
[data-bs-theme="dark"] .ui-feedback-danger  { color: #ea868f; }
[data-bs-theme="dark"] .picker-grid-resizer { background: #2b2f33; border-color: #444a52; }
[data-bs-theme="dark"] .apply-toolbar       { background: #2b2f33; border-color: #444a52; }
[data-bs-theme="dark"] .ui-col-divider,
[data-bs-theme="dark"] .ui-plot-h-divider { background: #444a52; }
[data-bs-theme="dark"] .ui-col-divider:hover,
[data-bs-theme="dark"] .ui-col-divider.dragging,
[data-bs-theme="dark"] .ui-plot-h-divider:hover,
[data-bs-theme="dark"] .ui-plot-h-divider.dragging { background: #6c757d; }
[data-bs-theme="dark"] .preset-scroller .btn {
    color: #e9ecef;
    border-color: #6c757d;
    background: #2b2f33;
}
[data-bs-theme="dark"] .preset-scroller .btn.active {
    background: #0083FF;
    border-color: #0083FF;
    color: white;
}
[data-bs-theme="dark"] .ag-theme-alpine-dark {
    --ag-background-color: #1a1d20;
    --ag-foreground-color: #e9ecef;
    --ag-header-background-color: #2b2f33;
    --ag-row-hover-color: #2b2f33;
    --ag-border-color: #444a52;
    --ag-secondary-foreground-color: #adb5bd;
}
[data-bs-theme="dark"] .dropdown-menu      { background-color: #2b2f33; border-color: #444a52; }
[data-bs-theme="dark"] .dropdown-item      { color: #e9ecef; }
[data-bs-theme="dark"] .dropdown-item:hover { background-color: #444a52; }
[data-bs-theme="dark"] .modal-content      { background-color: #2b2f33; color: #e9ecef; }
[data-bs-theme="dark"] .form-control,
[data-bs-theme="dark"] .form-select {
    background-color: #1a1d20;
    color: #e9ecef;
    border-color: #444a52;
}
[data-bs-theme="dark"] .form-check-input {
    background-color: #2b2f33;
    border-color: #6c757d;
}
/* Brand-navy buttons (#000072) are too dark on the dark body. Use the
   already-reserved brand bright blue instead. */
[data-bs-theme="dark"] .btn-primary {
    --bs-btn-bg: #0083FF;
    --bs-btn-border-color: #0083FF;
    --bs-btn-hover-bg: #1f97ff;
    --bs-btn-hover-border-color: #1f97ff;
    --bs-btn-active-bg: #1f97ff;
    --bs-btn-active-border-color: #1f97ff;
    --bs-btn-disabled-bg: #0083FF;
    --bs-btn-disabled-border-color: #0083FF;
}
[data-bs-theme="dark"] .btn-outline-primary {
    --bs-btn-color: #0083FF;
    --bs-btn-border-color: #0083FF;
    --bs-btn-hover-bg: #0083FF;
    --bs-btn-hover-border-color: #0083FF;
    --bs-btn-hover-color: white;
    --bs-btn-active-bg: #0083FF;
    --bs-btn-active-border-color: #0083FF;
    --bs-btn-disabled-color: #0083FF;
    --bs-btn-disabled-border-color: #0083FF;
}
/* Connection-status dropdown in the navbar — dbc.DropdownMenu(color=
   "light") renders btn-light, which is too pale on the dark navbar. */
[data-bs-theme="dark"] .navbar .btn-light,
[data-bs-theme="dark"] .navbar .dropdown-toggle {
    background-color: #2b2f33;
    border-color: #444a52;
    color: #e9ecef;
}
[data-bs-theme="dark"] .navbar .btn-light:hover {
    background-color: #3a3f44;
    border-color: #6c757d;
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
            # Dummy Output target for the once-only clientside callback
            # that wires the column + plot dividers' mouse events.
            dcc.Store(id="divider-init"),
            # Output target for the navbar 1 / 2 column-mode toggle.
            dcc.Store(id="col-mode-state"),
            # Current theme — 'light' (default) or 'dark'. The theme-
            # toggle clientside callback writes it; _aggregate (options.py)
            # echoes it into the plot-options Store so the plot
            # callback re-fires with the matching Plotly template.
            dcc.Store(id="theme", data="light"),
            dcc.Location(id="url", refresh=False),
            dbc.Navbar(
                dbc.Container(
                    [
                        dbc.NavbarBrand("datalab-plot"),
                        # Right-aligned cluster: layout toggle, theme
                        # toggle, then the connection-status component.
                        html.Div(
                            [
                                dbc.ButtonGroup(
                                    [
                                        dbc.Button(
                                            "▯",
                                            id="col-mode-1",
                                            color="secondary",
                                            outline=True,
                                            size="sm",
                                            title="Single-column layout",
                                        ),
                                        dbc.Button(
                                            "▯▯",
                                            id="col-mode-2",
                                            color="secondary",
                                            outline=True,
                                            size="sm",
                                            active=True,
                                            title="Two-column layout",
                                        ),
                                    ],
                                    size="sm",
                                    className="me-2",
                                ),
                                dbc.ButtonGroup(
                                    [
                                        dbc.Button(
                                            # U+25EF LARGE CIRCLE. The
                                            # natural sun glyph U+2600
                                            # has an emoji variant in
                                            # most fonts and still
                                            # renders heavyweight even
                                            # with VS-15. A flat circle
                                            # pairs cleanly with the
                                            # crescent moon ☾ — both
                                            # are line-art symbols with
                                            # no emoji counterpart.
                                            "◯",
                                            id="theme-light",
                                            color="secondary",
                                            outline=True,
                                            size="sm",
                                            active=True,
                                            title="Light mode",
                                        ),
                                        dbc.Button(
                                            "☾",
                                            id="theme-dark",
                                            color="secondary",
                                            outline=True,
                                            size="sm",
                                            title="Dark mode",
                                        ),
                                    ],
                                    size="sm",
                                    className="me-3",
                                ),
                                html.Div(connection.layout()),
                            ],
                            className="ms-auto d-flex align-items-center",
                        ),
                    ],
                    fluid=True,
                ),
                color="light",
                className="mb-2 border-bottom",
            ),
            html.Div(id="connect-prompt"),
            # Two-column shell with a draggable divider.
            # Left rail = data discovery (search + picker + staging).
            # Right pane = plot + plot options + export.
            # The divider's clientside callback (registered below) updates
            # the .ui-two-col CSS variable --left-col-width on drag.
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(search.layout(),         className="ui-section"),
                            html.Div(picker.layout(),         className="ui-section"),
                            html.Div(staging.layout(),        className="ui-section"),
                            html.Div(options.config_layout(), className="ui-section"),
                            html.Div(export.layout(),         className="ui-section"),
                        ],
                        id="left-col",
                        className="ui-col-left",
                    ),
                    html.Div(id="col-divider", className="ui-col-divider"),
                    html.Div(
                        [
                            html.Div(options.preset_layout(), className="ui-section"),
                            # Conditional sub-view row for Cycle Life preset.
                            # Hidden by default; revealed by `_toggle_sumview`
                            # when opt-preset == "Cycle Life".
                            html.Div(
                                options.summary_view_layout(),
                                className="ui-section pt-0",
                            ),
                            html.Div(plotting_panel.layout(), className="ui-section"),
                        ],
                        id="right-col",
                        className="ui-col-right",
                    ),
                ],
                id="main-content",
                className="ui-two-col",
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
    from dash import Input, Output, State

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
                ),
            )
        return {}, ""

    # Divider drag (both vertical column divider and horizontal plot
    # divider). Clientside JS — bound once via window.__dividerBound.
    # Each drag updates a CSS variable that drives layout; mouseup
    # dispatches a window resize so Plotly reflows. Separately, two
    # ResizeObservers keep the #opt-plot-w-px / #opt-plot-h-px inputs
    # in sync with the current layout — they fire on first reveal
    # (display:none → visible), window resize, divider drag, and
    # column-mode toggle. This replaced a brittle one-shot setTimeout
    # that ran before the connect-gate revealed the layout, leaving
    # the inputs stuck at 0.
    app.clientside_callback(
        """
        function(_) {
            if (window.__dividerBound) return window.dash_clientside.no_update;
            const container = document.getElementById('main-content');
            if (!container) return window.dash_clientside.no_update;
            window.__dividerBound = true;

            // --- Vertical column divider (#col-divider) ---
            const vDiv = document.getElementById('col-divider');
            if (vDiv) {
                let vDragging = false;
                vDiv.addEventListener('mousedown', (e) => {
                    vDragging = true;
                    vDiv.classList.add('dragging');
                    document.body.style.cursor = 'ew-resize';
                    document.body.style.userSelect = 'none';
                    e.preventDefault();
                });
                document.addEventListener('mousemove', (e) => {
                    if (!vDragging) return;
                    const rect = container.getBoundingClientRect();
                    const pct = ((e.clientX - rect.left) / rect.width) * 100;
                    const clamped = Math.max(25, Math.min(85, pct));
                    container.style.setProperty('--left-col-width', clamped + '%');
                });
                document.addEventListener('mouseup', () => {
                    if (!vDragging) return;
                    vDragging = false;
                    vDiv.classList.remove('dragging');
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                    window.dispatchEvent(new Event('resize'));
                });
            }

            // --- Horizontal plot-height divider (#plot-h-divider) ---
            const hDiv = document.getElementById('plot-h-divider');
            const resizer = document.getElementById('plot-resizer');
            if (hDiv && resizer) {
                let hDragging = false;
                hDiv.addEventListener('mousedown', (e) => {
                    hDragging = true;
                    hDiv.classList.add('dragging');
                    document.body.style.cursor = 'ns-resize';
                    document.body.style.userSelect = 'none';
                    e.preventDefault();
                });
                document.addEventListener('mousemove', (e) => {
                    if (!hDragging) return;
                    const rect = resizer.getBoundingClientRect();
                    // newH = mouseY relative to resizer top - half divider height.
                    const newH = e.clientY - rect.top - 3;
                    const clamped = Math.max(
                        200, Math.min(window.innerHeight * 0.92, newH)
                    );
                    resizer.style.setProperty('--plot-height', clamped + 'px');
                });
                document.addEventListener('mouseup', () => {
                    if (!hDragging) return;
                    hDragging = false;
                    hDiv.classList.remove('dragging');
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                    window.dispatchEvent(new Event('resize'));
                });
            }

            // --- ResizeObservers keep #opt-plot-w-px / #opt-plot-h-px
            // in sync with the current layout, including the first
            // reveal from display:none. Guard width > 0 so an observed
            // 0 (e.g. while the connect-gate hides main-content) does
            // not overwrite a prior good value.
            if (window.ResizeObserver) {
                const rightCol = document.getElementById('right-col');
                const wInput = document.getElementById('opt-plot-w-px');
                if (rightCol && wInput) {
                    new ResizeObserver(() => {
                        const w = Math.round(
                            rightCol.getBoundingClientRect().width
                        );
                        if (w > 0) wInput.value = w;
                    }).observe(rightCol);
                }
                const hInput = document.getElementById('opt-plot-h-px');
                if (resizer && hInput) {
                    new ResizeObserver(() => {
                        const cs = getComputedStyle(resizer);
                        const px = parseFloat(
                            cs.getPropertyValue('--plot-height')
                        );
                        if (isFinite(px) && px > 0) {
                            hInput.value = Math.round(px);
                        }
                    }).observe(resizer);
                }
            }
            return window.dash_clientside.no_update;
        }
        """,
        Output("divider-init", "data"),
        Input("connection-version", "data"),
    )

    # --- Width input → --left-col-width on #main-content -----------------
    # Fires on Enter (n_submit) so we don't relayout on every keystroke.
    app.clientside_callback(
        """
        function(_n, px) {
            if (!px) return window.dash_clientside.no_update;
            const main = document.getElementById('main-content');
            if (!main) return window.dash_clientside.no_update;
            const totalW = main.getBoundingClientRect().width;
            // Right column ~= totalW * (1 - leftPct/100) - 6px divider.
            // Solve: leftPct = (1 - (px + 6) / totalW) * 100
            const leftPct = Math.max(
                25, Math.min(85, (1 - (px + 6) / totalW) * 100)
            );
            main.style.setProperty('--left-col-width', leftPct + '%');
            window.dispatchEvent(new Event('resize'));
            return window.dash_clientside.no_update;
        }
        """,
        Output("opt-plot-w-px", "n_blur"),
        Input("opt-plot-w-px", "n_submit"),
        State("opt-plot-w-px", "value"),
        prevent_initial_call=True,
    )

    # --- Height input → --plot-height on #plot-resizer -------------------
    app.clientside_callback(
        """
        function(_n, px) {
            if (!px) return window.dash_clientside.no_update;
            const resizer = document.getElementById('plot-resizer');
            if (!resizer) return window.dash_clientside.no_update;
            const clamped = Math.max(
                200, Math.min(window.innerHeight * 0.92, px)
            );
            resizer.style.setProperty('--plot-height', clamped + 'px');
            window.dispatchEvent(new Event('resize'));
            return window.dash_clientside.no_update;
        }
        """,
        Output("opt-plot-h-px", "n_blur"),
        Input("opt-plot-h-px", "n_submit"),
        State("opt-plot-h-px", "value"),
        prevent_initial_call=True,
    )

    # Column-mode toggle [1] / [2] in navbar.
    app.clientside_callback(
        """
        function(c1, c2) {
            const triggered = window.dash_clientside.callback_context.triggered_id;
            if (!triggered) {
                return [window.dash_clientside.no_update,
                        window.dash_clientside.no_update,
                        window.dash_clientside.no_update];
            }
            const main = document.getElementById('main-content');
            if (!main) {
                return [window.dash_clientside.no_update,
                        window.dash_clientside.no_update,
                        window.dash_clientside.no_update];
            }
            if (triggered === 'col-mode-1') {
                main.classList.add('force-single-col');
                window.dispatchEvent(new Event('resize'));
                return ['single', true, false];
            } else {
                main.classList.remove('force-single-col');
                window.dispatchEvent(new Event('resize'));
                return ['two', false, true];
            }
        }
        """,
        Output("col-mode-state", "data"),
        Output("col-mode-1", "active"),
        Output("col-mode-2", "active"),
        Input("col-mode-1", "n_clicks"),
        Input("col-mode-2", "n_clicks"),
        prevent_initial_call=True,
    )

    # Theme toggle [◯ | ☾] ButtonGroup in navbar. Three behaviours
    # share one callback:
    #   * initial layout (no triggered_id) — match the OS theme via
    #     `prefers-color-scheme`. Page-load only; user toggle wins
    #     afterwards.
    #   * theme-light / theme-dark click — manual override for the
    #     session.
    # Each path flips `data-bs-theme` on <html> (chrome CSS), swaps
    # both AG Grids' className (alpine ↔ alpine-dark), toggles the
    # active prop on both buttons, and writes the theme Store. The
    # plot follows because plotting_panel._render_plot has
    # Input("theme") and treats it as a render trigger.
    app.clientside_callback(
        """
        function(nL, nD) {
            const trig = window.dash_clientside.callback_context.triggered_id;
            let next;
            if (!trig) {
                // Initial fire — match OS preference.
                const sysDark = window.matchMedia &&
                    window.matchMedia('(prefers-color-scheme: dark)').matches;
                next = sysDark ? 'dark' : 'light';
            } else {
                next = (trig === 'theme-dark') ? 'dark' : 'light';
            }
            document.documentElement.setAttribute('data-bs-theme', next);
            const cls = (next === 'dark')
                ? 'ag-theme-alpine-dark'
                : 'ag-theme-alpine';
            return [next, next === 'light', next === 'dark', cls, cls];
        }
        """,
        Output("theme", "data"),
        Output("theme-light", "active"),
        Output("theme-dark", "active"),
        Output("picker-grid", "className"),
        Output("staged-grid", "className"),
        Input("theme-light", "n_clicks"),
        Input("theme-dark", "n_clicks"),
    )

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
