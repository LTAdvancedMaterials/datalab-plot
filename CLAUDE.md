# CLAUDE.md — agent guide for datalab-plot

Project context for AI agents working in this repo. This codebase is written
entirely by agents, for internal use by a small team. Keep it legible.

## What this is

`datalab-plot` plots electrochemistry / spectroscopy data pulled from a
[datalab](https://github.com/datalab-org/datalab) instance — locally, without
routing through the datalab server. Three surfaces:

- **Dash GUI** — `datalab-plot gui` (interactive browsing + plotting in a
  browser tab on `http://localhost:8501`).
- **Python API** — `plot_cycles`, `plot_cell`, `find_cells` (matplotlib
  figures, suitable for notebooks / scripts).
- **CLI** — `datalab-plot list | cycle | cell | nmr | xrd | uvvis` (one-shot
  PNG export to disk).

The codebase used to ship a parallel Streamlit GUI; it was removed and the
Dash GUI is the sole web front-end.

## Architecture

Data flows in one direction — keep it that way:

```
client.py / search.py   fetch items + files from datalab (with a local cache)
        │
        ▼
parsers/   (echem, nmr, xrd, uvvis)   raw files → pandas DataFrames
        │
        ▼
series.py   DataFrames → render-agnostic plot series (NamedTuples)
        │
        ├─► plots/             matplotlib rendering (Python API + CLI)
        └─► plotly_builders.py Plotly rendering (Dash GUI)
```

- `parsers/` and `series.py` are **pure** (no UI framework, no I/O) — this
  is the tested layer. Put new data logic here, **not** in `plotly_builders.py`
  or `plots/`.
- `plotly_builders.py` and `plots/` are **rendering only**. They must not
  re-implement data transforms — if you find yourself filtering cycles or
  computing dQ/dV in a renderer, move it to `series.py` and have both
  backends call it.
- The Dash UI under `gui_dash/` is the **only** code that imports `dash`,
  `dash_bootstrap_components`, `dash_ag_grid`, or `flask`. Everything else
  is framework-free.
- No circular imports. Dependency direction is strictly top-to-bottom above.

### Where things live (single-source-of-truth map)

| Module | Purpose | Touch-it? |
|---|---|---|
| [src/datalab_plot/client.py](src/datalab_plot/client.py) | `DatalabPlotClient` — datalab API wrapper with on-disk file cache. | Yes |
| [src/datalab_plot/search.py](src/datalab_plot/search.py) | `find_cells`, `extract_cathode_mass_mg`. | Yes |
| [src/datalab_plot/cache.py](src/datalab_plot/cache.py) | `cache_dir()` — resolves `$DATALAB_PLOT_CACHE` / cwd / platformdirs. | Rarely |
| [src/datalab_plot/credentials.py](src/datalab_plot/credentials.py) | Pure load/save/forget of API keys + the `connect()` helper. | Yes |
| [src/datalab_plot/csv_export.py](src/datalab_plot/csv_export.py) | `figure_to_csv` / `figure_to_csv_tabs` — used by the GUI export. | Rarely |
| [src/datalab_plot/parsers/](src/datalab_plot/parsers/) | `echem`, `nmr`, `xrd`, `uvvis` — raw files → DataFrames. **Pure.** | Yes |
| [src/datalab_plot/series.py](src/datalab_plot/series.py) | DataFrame → plot-series NamedTuples (`SummarySeries`, `CycleTrace`, ...). **Pure.** | Yes |
| [src/datalab_plot/plot_constants.py](src/datalab_plot/plot_constants.py) | `PlotStyle`, `PRESET_*`, `AXIS_*`, `STATUS_COLOR_MAP`, `PLOT_OPTION_DEFAULTS`, `_ENV_*`. | Yes |
| [src/datalab_plot/plot_helpers.py](src/datalab_plot/plot_helpers.py) | Color / axis helpers (`_rgba_to_css`, `_axis_label`, ...). **Pure.** | Yes |
| [src/datalab_plot/plotly_builders.py](src/datalab_plot/plotly_builders.py) | All Plotly figure builders + the central `build_figure_for_payload`. **Pure.** | Yes — see "Visual consistency" below |
| [src/datalab_plot/picker_helpers.py](src/datalab_plot/picker_helpers.py) | `_build_initial_df` — search-results → picker DataFrame. **Pure.** | Rarely |
| [src/datalab_plot/plots/](src/datalab_plot/plots/) | Matplotlib renderers for Python-API / CLI. | Yes |
| [src/datalab_plot/picker.py](src/datalab_plot/picker.py) | Jupyter ipywidgets picker. Unrelated to the GUI. | Rarely |
| [src/datalab_plot/cli.py](src/datalab_plot/cli.py) | Argparse CLI + `datalab-plot gui` launcher. | Yes |
| [src/datalab_plot/gui_dash/](src/datalab_plot/gui_dash/) | The Dash web GUI — see "Dash GUI" below. | Yes |

## Conventions

- Python **3.12 only** (`requires-python = ">=3.12,<3.13"`). This is forced by
  `navani` pinning `numpy<2`; numpy 1.26 only ships cp312 wheels. Do not bump.
- All modules start with `from __future__ import annotations`.
- Use `logging.getLogger(__name__)` — never `print` (except in `cli.py`, where
  stdout/stderr is the user interface). Log caught exceptions; don't swallow
  them silently.
- Tests use **synthetic in-memory data only** (see `tests/conftest.py`). Do
  not commit real company data files.
- **Neware `.nda` / `.ndax` data is post-processed.** `navani.neware.
  neware_reader_nda` only classifies three of the 19 Neware `Status` values
  (`Rest`, `CC_Chg`, `CC_DChg`); everything else (`CV_Chg`, `CCCV_Chg`,
  `CP_Chg`, `CV_DChg`, `Pause`, `OCV`, …) becomes the categorical literal
  `"unknown"`, which then reads as a distinct state in navani's half-cycle
  diff and inflates the cycle count (each CC↔CV transition becomes a
  spurious half cycle, shifting discharge halves onto the next cycle's
  trace). `parsers.echem._normalise_neware_state` runs after every
  `load_echem` call and re-derives `state` / `cycle change` / `half cycle`
  / `full cycle` / `Capacity` from `Status`. No-op for non-Neware data. If
  you ever rip out the post-processor, V-Q on Neware files will fragment
  again.
- **V-Q plots drop rest rows.** `series.voltage_capacity_series` filters
  `state == 'R'` before splitting half-cycles. Rest periods sit at constant
  Q while V relaxes, so plotting them draws vertical "OCV recovery" lines
  that aren't part of the cycling characteristic. Time-domain plots
  (`voltage_time_series`) and cycle-summary plots keep rests; capacity-
  domain plots don't.

## Dash GUI

Launched via `datalab-plot gui` (`cli.py:_cmd_gui`). The Dash app lives in
[src/datalab_plot/gui_dash/](src/datalab_plot/gui_dash/):

```
gui_dash/
├── app.py            Dash app, top-level layout, global CSS, main()
├── state.py          Per-Flask-session in-memory dict (≈ st.session_state)
├── connection.py     Navbar Connect dropdown + modal + auto-connect
├── search.py         Search input + button; populates picker
├── picker.py         "Search results" AG Grid — browse-only, with Add-to-plot
├── staging.py        "Plotting" AG Grid — durable, editable label/group/color
├── options.py        Preset segmented control + Plot-options collapsible
├── plotting_panel.py Main dcc.Graph (stable mount) + Cycle Life sub-views
├── export.py         CSV + Save / Load plot config (JSON)
├── plot_io.py        Pure save/load helpers for plot-config JSONs
└── assets/
    └── favicon.svg   Brand mark (also referenced by app.index_string)
```

### Layout pattern

**Two columns** in a CSS-Grid shell (`.ui-two-col` in `_GLOBAL_CSS`) with a
**vertical 6-px draggable divider** between them. Wide viewports (≥ 992
px) get the two-column split; narrow viewports collapse to a single
column and the divider is hidden. The navbar exposes a **1 / 2** button
group that forces single-column even on a wide viewport (via the
`.force-single-col` class on `#main-content`).

Default split: **60 % left / 40 % right**, controlled by the CSS
variable `--left-col-width` on `#main-content`. A clientside JS callback
in `_make_app` wires both dividers' `mousedown` → `mousemove` →
`mouseup` flow, clamps the new percentage to `[25 %, 85 %]` (vertical) or
`[200 px, 92 vh]` (horizontal plot divider), and on release dispatches a
`window.resize` event so Plotly reflows.

```
[Navbar: brand                           [▯|▯▯] [☀|☾] [✓ServerName ▾] ]
┌─ #main-content.ui-two-col (CSS Grid: --left-col-width | 6px | 1fr) ──┐
│ #left-col.ui-col-left (60%)  │ #col-divider │ #right-col.ui-col-right│
│ — data + controls + export   │ align-self:  │ — preset + plot only   │
│ (scrolls naturally)          │ stretch      │ (position: sticky;     │
│                              │              │  pinned to viewport)   │
│ [Search]                     │              │ [Preset selector]      │
│ [Search results]             │              │   V vs t | V vs Q | …  │
│  – AG Grid                   │              │ [Sub-view selector]    │
│  – ✓ column + row tint       │              │   Discharge | Charge   │
│    for already-staged items  │              │   | CE % | Table       │
│  – + Add to plot             │              │   (Cycle Life only;    │
│ [Plotting]                   │              │    hidden otherwise)   │
│   apply-toolbar + grid all   │              │ [Plot]                 │
│   inside one Collapse        │              │   onboarding-hint      │
│ [Plot options]               │              │   #plot-resizer (with  │
│   collapsible (incl. Plot    │              │     --plot-height CSS  │
│   size X/Y px inputs) +      │              │     var)               │
│   summary-extras +           │              │     dcc.Graph          │
│   cache caption +            │              │       .ui-plot-graph   │
│   Re-fetch / Auto-refresh    │              │     #plot-h-divider    │
│ [Export]                     │              │       (ns-resize drag) │
│   PNG hint · CSV ·           │              │   warnings             │
│   Save / Load JSON (load     │              │                        │
│   fires on file selection)   │              │                        │
└──────────────────────────────────────────────────────────────────────┘
```

**Plot height is CSS-driven via a CSS variable on `.ui-plot-resizer`.**
The variable `--plot-height` defaults to `calc(100vh - 11rem)` on wide
viewports and `60vh` on narrow ones (or in `.force-single-col` mode).
The horizontal-divider clientside JS overrides it on drag via
`style.setProperty('--plot-height', ...)`. The `dcc.Graph` instances
read it through the `.ui-plot-graph` rule `height: var(--plot-height)
!important`. Plot **width** comes from the right column's width.
Plotly's `responsive: True` (in `_PLOTLY_CONFIG`) plus the dispatched
resize event makes the figure recompute when either divider moves.

### Navbar toggles

| Control | Effect |
|---|---|
| `[▯] [▯▯]` button group | Toggles `.force-single-col` on `#main-content`. `[▯▯]` (two-column) is active by default. Stays force-single until `[▯▯]` is clicked or the page is reloaded. The single white-rectangle / double white-rectangle glyphs (U+25AF) mirror the layout visually. |
| `[◯] [☾]` button group | Toggles `data-bs-theme="dark"` on `<html>` AND writes `dcc.Store(id="theme")`. On first load, the theme matches the OS preference via `prefers-color-scheme` — the same clientside callback fires with `triggered_id=null` on initial layout and reads `window.matchMedia('(prefers-color-scheme: dark)')`. Manual clicks override for the session; refreshing re-applies the OS preference. Glyphs are flat line-art (U+25EF LARGE CIRCLE, U+263E LAST QUARTER MOON) — both monochrome with no emoji counterpart, so they pair visually. The toggle flips: page chrome (`[data-bs-theme="dark"]` CSS overrides), both AG Grids' className (`ag-theme-alpine` ↔ `ag-theme-alpine-dark`), the Plotly figure template (`plotly_white` ↔ `plotly_dark`), the staged-row tint (`--ag-row-staged-bg`) and editable-cell tint (`--ag-editable-cell-bg`). The plot re-renders on theme change even when Auto-refresh is off — `_render_plot` has `Input("theme", "data")` and treats it as a render trigger (without `force_refresh`). |
| `✓ ServerName ▾` dropdown (when connected) | Cache stats header + Forget cached data / Forget saved key / Sign out. |
| `Connect` button (when disconnected) | Opens the credentials modal. |

### State management

Two layers:

1. **`dcc.Store` for serialisable UI state**, defined in `app.py`. Used as
   triggers — incrementing a version Store fires every callback listening
   to it:
   - `connection-version` — bumped on connect / sign-out / dropdown actions.
   - `search-version` — bumped after a successful search or auto-populate.
   - `staging-version` — bumped after Add-to-plot / Remove / Apply / cell-
     edit on the staged grid.
   - `save-version` — bumped after a plot-config save / delete.
   - `picker-payload` — the staged-set's plot payload (`{label → {item_id,
     group?, color?}}`); the plot callback's primary Input.
   - `plot-options` — aggregated options widget values; the plot callback's
     other primary Input.
   - `opt-preset` — current preset string (driven by clicks on the preset
     button group, not by `dbc.RadioItems`).
   - `plot-version` — bumped when the plot callback finishes.

2. **`get_state()` returns a per-Flask-session in-memory dict** (in
   `gui_dash/state.py`). Holds non-serialisable objects:
   - `client` — `DatalabPlotClient`
   - `server_name`, `auto_connect_failed`, `auto_connect_source`, `signed_out`
   - `results` (the last search-results DataFrame), `picker_initial`
   - `staged_items` (list of dicts — the durable plot set)
   - `raw_data` (dict[item_id → parsed DataFrame] — in-memory parse cache)
   - `cathode_masses` (dict[item_id → grams])
   - `broken_items` (dict[item_id → error message])
   - `last_fig`, `last_plot`, `last_cycle_summaries`

`clear_state()` wipes the per-session dict on sign-out.

### Critical Dash gotchas

- **dcc.Graph must be mounted at layout-time and updated ONLY via its
  `figure` prop.** Plotly preserves UI state (zoom/pan/legend toggles)
  via `layout.uirevision` only when the React component is the same
  instance across renders. If you return `dcc.Graph` from a callback that
  outputs to a parent's `children`, React remounts the component and
  Plotly drops its UI state. The current `plotting_panel.py` mounts
  `dcc.Graph(id="main-plot")` once in `layout()` and outputs to
  `main-plot.figure`. Don't refactor away from this.

- **`dcc.Loading` only with `overlay_style` + `target_components`.**
  The default mode swaps `children` out for the spinner, which would
  unmount the Graph (collapsing the height + dropping uirevision).
  The v2 overlay API in `plotting_panel.py` uses
  `overlay_style={"visibility": "visible", ...}` so the Graph stays
  mounted and a translucent overlay layers on top. `target_components=
  {"main-plot": "figure", "tabs-plot-container": "children"}` makes
  the spinner fire only when the plot callback's specific Outputs are
  being computed — not on every unrelated callback. `delay_show=200`
  suppresses flicker on styling-only re-renders.

- **`uirevision` is keyed on `mode|x|y|y2|cycle`** in `_ui_revision()`. Same
  revision → zoom preserved across styling changes; different revision
  (mode switch, axis switch, cycle number change) → zoom resets, which is
  the right behaviour.

- **`dbc.RadioItems` cannot make a connected btn-group.** It wraps every
  input + label in `<div class="form-check">`, which breaks Bootstrap's
  `.btn-group > .btn:not(:first-child)` selector. The preset selector
  (`options.py`) uses `dbc.ButtonGroup` of individual `dbc.Button`s with
  pattern-matching ids `{"type": "opt-preset-btn", "value": p}` and an
  `active` prop driven by a click callback. Don't switch back to
  `dbc.RadioItems` no matter how tempting.

- **AG Grid uses `getRowId="params.data.item_id"`** so updates preserve
  selection by id, not row index. Required for "Add to plot" /
  edit-cell-then-redraw flows to keep selection stable.

- **Plot height is CSS-driven via the `--plot-height` variable** on
  `.ui-plot-resizer` (the wrapper Div around the dcc.Graph) — figure
  layout's `height` is intentionally `None`. Don't pass a numeric
  `height` to the figure builders (`_layout`, `_plotly_*`,
  `build_figure_for_payload`) or set `figure.layout.height` from the
  Dash side. The `.ui-plot-graph` rule (`height: var(--plot-height)
  !important`) is the single source. The horizontal-divider drag updates
  the variable; defaults are in the media-query / `.force-single-col`
  rules. Same for width — the right column's width wins; don't set
  `figure.layout.width` or wrap the Graph in a padded Div.

- **Grid divider needs `align-self: stretch`.** The `.ui-two-col`
  container has `align-items: start`, which would collapse an empty
  divider div to 0 height. The `.ui-col-divider` rule overrides with
  `align-self: stretch` so the divider fills the row height. If you
  remove the override the divider disappears.

- **The right column is `position: sticky`.** Only the left column
  scrolls with the document; the right column (plot + h-divider + preset)
  stays pinned to the viewport top via `position: sticky; top: 0.5rem;
  align-self: start; max-height: calc(100vh - 1rem); overflow-y: auto`.
  The `align-self: start` is required for `position: sticky` to take
  effect inside a CSS Grid child (it opts out of the grid's `align-items:
  start` *and* prevents stretching to match the left column's height).
  The sticky behaviour is overridden to `static` by both `.force-single-
  col` and the `@media (max-width: 991.98px)` rule, so single-column
  mode and narrow viewports get natural document flow.

- **Dividers + navbar toggle rely on clientside callbacks bound once.**
  The `window.__dividerBound` flag in `app.py:_make_app`'s clientside JS
  guards against rebinding on every callback fire. The col-mode toggle
  has its own clientside callback. The same divider-bind callback also
  populates `#opt-plot-w-px` and `#opt-plot-h-px` on first paint and on
  every drag (mousemove for h-divider, mouseup for both). If you replace
  any of them or add another mousedown handler on `#col-divider` /
  `#plot-h-divider`, audit the bound flag and the callback chain.

- **Plot size X / Y inputs are bidirectional.** In Plot options, the
  `opt-plot-w-px` and `opt-plot-h-px` `dbc.Input(type="number")` fields
  reflect the current plot size (pushed by the divider JS) AND drive it
  on Enter (via two clientside callbacks that write the appropriate CSS
  variable: width → `--left-col-width` on `#main-content`, height →
  `--plot-height` on `#plot-resizer`). They fire on `n_submit` (Enter)
  rather than `value` to avoid relayout on every keystroke. Output is the
  field's own `n_blur` — a throwaway since Dash needs *some* Output but
  this prop never reads back to the callback.

- **Plotly template is parameter-driven.** Pass `theme="dark"` (→
  `plotly_dark`) into `build_figure_for_payload`; anything else maps
  to `plotly_white`. The Dash GUI threads this via the
  `dcc.Store(id="theme")` → `options._aggregate` (echoing the value
  into the `plot-options` Store) → `plotting_panel._render_plot`
  pipeline. Don't hardcode the template string inside any
  `_plotly_*` builder — `_layout()` accepts it as a kwarg.
  `_render_plot` also has `Input("theme", "data")` directly and
  treats it as a render trigger even when Auto-refresh is off, so
  toggling the theme always flips the figure immediately — without
  re-fetching data (`force_refresh` stays bound to `is_refresh`
  only). `_empty_figure(message, theme=)` accepts the same hint for
  placeholder states.

- **AG Grid styling uses inline CSS variables.** Two tints are
  defined per theme on `:root` and `[data-bs-theme="dark"]`:
  `--ag-row-staged-bg` (picker.py `getRowStyle` — already-staged
  rows) and `--ag-editable-cell-bg` (staging.py `_COLUMN_DEFS`
  `cellStyle` — the editable label/group/color cells). Changing
  either requires editing CSS, not Python. Inline CSS-variable
  references resolve at paint time, so theme switches re-tint
  without a grid re-render.

- **`opt-specific-capacity` and `opt-cycle` must ALWAYS be mounted.**
  Both are Inputs to `options._aggregate`, and a Dash callback cannot
  fire unless every one of its Input components exists in the current
  layout. `options._render_extras` therefore mounts BOTH in every mode
  and only toggles their `display` (specific-capacity visible in
  `summary`, cycle visible in `dqdv`, both hidden otherwise). Do NOT
  "optimise" it back to mounting only the relevant one per mode: that
  silently breaks `_aggregate` whenever you switch directly between the
  two modes that each mount only one widget (dQ/dV ↔ Cycle Life), so
  `plot-options` never updates and the plot freezes. (Other transitions
  hid the bug because the xy modes re-mount both.) Separately, the
  load-plot-config callback in `export.py` deliberately skips writing
  these two — they re-mount (resetting to defaults) on every mode
  change, which would race a same-callback write.

- **`prevent_initial_call="initial_duplicate"`** is required (not just
  `True`) on any callback whose Output has `allow_duplicate=True` AND
  needs to fire on initial page load. Otherwise Dash silently skips the
  initial call.

### Design system (in `app.py`'s `_GLOBAL_CSS`)

Brand palette:
- `#000072` navy — primary buttons + segmented-control active state
- `#E6EAEE` grey — navbar + apply-toolbar soft surface
- `#00FFBA` mint — connected-status dot (single use)
- `#FAB400` gold — warning-alert tint
- `#0083FF` bright blue — dark-mode primary (replaces navy, which goes
  illegibly dim on the dark body). Do not use it in the light theme — it
  pairs poorly with the navy primary.

Button hierarchy (single source of truth):
- **Solid primary** — modal Connect submit, navbar Connect (disconnected).
- **Outline primary** — Search, Refresh, Apply (×3 in staging), Save,
  Add to plot.
- **Outline secondary** — All / None / Invert, Cancel, Dismiss, Export
  CSV, Delete, Remove selected.
- **Link** — collapse/expand chevrons. Apply `ui-section-title` to the
  primary collapse toggle (e.g. `▾ Search results`, `▾ Plotting`,
  `▸ Plot options`), and `ui-caption` to secondary accessory links
  (e.g. `⤢ Expand` / `⤡ Compact`). Never `text-secondary` directly —
  use the semantic class.

All `dbc.Button` and `dbc.Input` use `size="sm"`. Don't introduce default-
size buttons or default-size inputs — they break the visual rhythm.

Containers:
- `.ui-section` — every panel; `padding-bottom: 0.75rem; margin-bottom:
  0.75rem; border-bottom: 1px solid #f1f3f5;` (last child suppressed).
- `.picker-grid-resizer` — wraps both AG Grids; white bg, `#dee2e6`
  border, `border-radius: 0.375rem`, `height: 400px` (or `80vh` with
  `.expanded`).
- `.apply-toolbar` — brand-grey bg, same border / radius as the grid
  wrapper.

### Typographic style guide

Every text element in the Dash GUI uses one of these semantic classes
from `_GLOBAL_CSS` in `gui_dash/app.py`. **Do not introduce ad-hoc
`className="small"`, `fw-bold`, or inline `font-size`** — apply the right
class. New agents who need a text style they think isn't covered should
extend the class table here, not invent a one-off.

| Use | Class | Example |
|---|---|---|
| Section title (collapse-toggle headers) | `ui-section-title` | `▾ Search results`, `▸ Plot options` |
| Subsection label (inside Options accordion + apply-toolbar prompt) | `ui-subsection-label` | `AXES & TITLE`, `APPLY TO 3 SELECTED:` |
| Field label (above / beside an input) | `ui-field-label` | `Mode`, `x min`, `API key` |
| Status / counts | `ui-meta` (+ `<strong>` for emphasis) | `3 selected of 30`, `5 staged` |
| Helper caption / hint | `ui-caption` | `PNG: hover the plot…`, `Leave blank for auto-range.` |
| Feedback success | `ui-feedback ui-feedback-success` | `Saved to my-plot.json` |
| Feedback error | `ui-feedback ui-feedback-danger` | `Save failed` |
| Brand wordmark (navbar only) | `navbar-brand` (Bootstrap auto-applies) | `datalab-plot` |

#### Rules

- **Type scale — 5 sizes only**: 17px (navbar brand) / 16px (body) / 15px
  (`--text-md`, section titles) / 13px (`--text-sm`) / 11px (`--text-xs`).
  Never set `font-size` inline. The one tolerated exception is a relative
  size (`em`-based) on a decorative glyph that should track its
  surrounding text — see `.connection-status-dot`.
- **Weight — 2 weights only**: 400 regular, 600 semibold. **Never `fw-bold`
  (700)**. `<strong>` is globally rebalanced to 600 in `_GLOBAL_CSS`.
- **Color**: never combine `text-muted` with semibold weight outside of
  `.ui-subsection-label` (the one legitimate exception, justified by its
  uppercase + letter-spacing that makes it read as a section delimiter,
  not as de-emphasised body).
- **Spacing**: gutters always `g-2`; "within a section" rows `mb-2`;
  "between subsection groups" `mb-3`. **Never use `mt-*`** — push space
  downward via `mb-*` on the preceding element. This way, deleting or
  reordering a row never breaks vertical rhythm.
- **Alerts**: just `dbc.Alert(text, color="warning" | "danger" | "info")`.
  The `.alert` rule in `_GLOBAL_CSS` standardises padding + size. Never
  add `className="py-1 px-2 mb-1"` — that's a leak from a past life.

#### Layout patterns to follow

**Section header row** (the chevron collapse-toggle used by Search Results
/ Plotting / Plot options):

```python
dbc.Button(["▾ ", "Search results"],     # plain string, not html.Strong
           color="link", size="sm",
           className="p-0 text-decoration-none ui-section-title")
```

**Field-label + input** (vertical, inside Options accordion):

```python
dbc.Col([
    dbc.Label("Mode", className="ui-field-label"),
    dbc.Select(id="opt-mode", size="sm", ...),
], width=2)
```

**Status counts** (everywhere):

```python
html.Span([html.Strong(f"{n}"), " staged"], className="ui-meta")
# renders: 5 staged   — semibold number, muted text
```

#### Common pitfalls (don't do these)

- ❌ `className="small text-muted"` for a section header — looks like a
  caption. Use `ui-subsection-label`.
- ❌ `className="small fw-bold text-muted"` — bold + muted is visually
  contradictory. Use either `ui-section-title` (dark, semibold) or
  `ui-subsection-label` (muted, semibold, uppercase).
- ❌ `html.Small(...)` — duplicates the size system. Use a `<span>` with
  `ui-caption` instead.
- ❌ Inline `style={"fontSize": "0.8125rem"}` — bypasses the type scale.
- ❌ Mixing `g-1` with `g-2` in adjacent rows — pick `g-2`, stay with it.
- ❌ Per-alert padding (`py-1 px-2 mb-1`) — `.alert` is already standardised.
- ❌ `className="fw-bold"` anywhere — semibold (600) via `<strong>` or a
  semantic class only. Never 700.
- ❌ `className="mt-*"` for anything — always push space via `mb-*` on the
  preceding element. The one tolerated exception is a label-spacer
  `dbc.Label(" ", className="ui-field-label")` placeholder used to
  vertically align a label-less Switch with its labelled neighbours in a
  flex row (see `options.py:_plot_options_body`'s colorbar column).

## Visual consistency between plot types

All Plotly figure builders in `plotly_builders.py` must follow these
shared conventions. Work on any single plot type should not diverge from
the others.

- **All figures go through `_layout()`** for margins, legend, font, grid,
  and borders. Never call `fig.update_layout()` with styling params
  directly in a figure builder — styling belongs in `_layout` or
  `PlotStyle`.
- **Colors come from `_assign_colors()`** (per-cell, tab10 or group-cmap)
  or `cycle_cmap()` (per-cycle gradient on V-Q only). Do not hard-code
  hex colors in figure builders.
- **Line widths are always `N * width_scale`**, where `N` is a per-plot-
  type base (1.0–1.6). This lets the width control in the options panel
  scale all plots uniformly.
- **Trace legend names use `_display_name(it)`**, not the raw `label`.
  This shows `"My Label  [CEL-085]"` when the label and item ID differ,
  so individual cells remain identifiable in large repeat stacks.
- **Hover templates always include the cell name in bold** as
  `<b>{display_name}</b>` so the user can read which cell a point belongs
  to without looking at the legend.
- **Half-cycle NaN gaps** (`split_half_cycles`) must be applied
  consistently: any capacity-domain or step-split trace that spans multiple
  half-cycles needs the gap separator so lines don't connect across the
  charge/discharge boundary. Omitting the gap on one plot type while
  another uses it is a bug.
- **`_xy_primary_status` traces are named by status value**, not by cell —
  this is intentional (one legend entry per step type shared across all
  cells). All other plot types are named per cell via `_display_name`.

## Save / load plot configs

`export.py` writes JSON configs to `cache_dir() / "saved_plots/"` (sibling
of the per-item file caches). Schema in `plot_io.py`:

```json
{
  "name": "team-A-vs-B-cycle-10",
  "saved_at": "2026-06-09T14:23:01Z",
  "datalab_url": "...",
  "staged_items": [{"item_id": "...", "name": "...", "label": "...",
                    "group": "...", "color": "..."}, ...],
  "preset": "V vs Q",
  "options": { ... aggregated plot-options Store ... }
}
```

- Filenames are sanitised to `[a-zA-Z0-9_-]`; the original name is
  preserved in the JSON.
- "Forget cached data" (in the connected dropdown) clears in-memory
  parsed data AND `shutil.rmtree`s every subdir under `client.cache_root`
  EXCEPT `saved_plots/` — saved configs survive the wipe.

## Working in this repo

Setup:

```sh
uv sync --all-extras      # installs runtime + gui + picker + dev tools
```

Run the full check loop before committing (there is no CI):

```sh
make check                # ruff lint + mypy + pytest  — must pass
```

Individual targets: `make lint`, `make types`, `make test`, `make fmt`
(format), `make cov` (coverage). Raw equivalents: `uv run ruff check
src tests`, `uv run mypy`, `uv run pytest`.

Run the app / CLI:

```sh
datalab-plot gui            # Dash GUI at http://localhost:8501
datalab-plot gui --no-browser --port 8060
datalab-plot --help         # one-shot CLI commands
```

Credentials: `DATALAB_URL` / `DATALAB_API_KEY` via env or `.env` (see
`.env.example`). The GUI also persists API keys per-URL under
`~/.config/datalab-plot/credentials.json` (chmod 0600) — see
`credentials.py`.

## Verifying GUI changes

There are **no automated tests for the Dash UI**. After non-trivial
GUI edits:

1. `make check` — confirms imports + the data-layer tests still pass.
2. Start the server in headless mode and curl the key endpoints to
   confirm Dash boots and the callback graph serialises cleanly:
   ```sh
   datalab-plot gui --no-browser
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_dash-layout
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_dash-dependencies
   ```
   All three should return `200`. If `_dash-dependencies` fails, you have
   a broken callback registration (e.g. an Output id that doesn't exist
   in the layout).
3. Manual smoke test in a browser if the change is user-visible.

## Common operations / recipes

- **Add a new plot mode**: extend `PRESET_OPTIONS` + `PRESET_MAP` in
  [plot_constants.py](src/datalab_plot/plot_constants.py), add a `_plotly_<mode>` builder in
  [plotly_builders.py](src/datalab_plot/plotly_builders.py) that follows the visual
  conventions above, and dispatch in `_build_plotly`. The preset
  segmented control picks it up automatically.

- **Add a new option to the Plot options accordion**: add a widget in
  `options._plot_options_body()` with a stable id, add the corresponding
  `Input("opt-<id>", "value")` to the `_aggregate` callback, and consume
  the resulting key from `plot-options` Store data inside the plot
  callback. Don't forget to add the same key to `_reset_options`'s
  outputs and `_load`'s outputs in `export.py` if you want save/load to
  cover it.

- **Add a new column to the staging grid**: extend `_COLUMN_DEFS` in
  `staging.py` and extend `build_payload()` if the new column should
  flow into the plot. If you want bulk-apply support, add a new field
  to `_apply_field` and wire it in `_apply_to_selection`.

- **Add a Cycle Life sub-view**: append a `(short_label, title)` tuple
  to `_SUMMARY_VIEWS` in [options.py](src/datalab_plot/gui_dash/options.py),
  then make sure `_plotly_summary` in
  [plotly_builders.py](src/datalab_plot/plotly_builders.py) emits a
  matching `(title, fig)` pair. The render-side dispatch in
  `plotting_panel._render_plot` and `_render_summary_subview` is a
  dict lookup by title, so the strings must match exactly. The
  reserved title `"Capacity table"` routes to the table layout
  instead of a Plotly figure.

- **Touch session-state keys**: any key in the `get_state()` dict is
  load-bearing across callbacks. Renaming requires auditing every
  reference. Prefer adding new keys to renaming.

## Out of scope (do not implement unless asked)

- Multi-user / multi-tenant deployment. This is a single-user local tool;
  state is per-Flask-session in-memory.
- Cross-instance plot-config portability. `staged_items` references
  `item_id`s that are instance-specific; the saved JSON includes
  `datalab_url` for reference but loading against a different instance
  will silently fail to find items.
- A "share via URL" feature.
- Persisting the *user's manual* theme override across page reloads.
  Initial theme matches the OS via `prefers-color-scheme`; manual
  toggles are session-local. Refresh re-applies the OS preference.
- Adapting trace palettes (STATUS_COLOR_MAP, tab10) to the dark
  template. The stock mid-saturation colours read on both
  backgrounds; revisit only if legibility complaints arise.
