# CLAUDE.md — agent guide for datalab-plot

Project context for AI agents working in this repo. This codebase is written
entirely by agents, for internal use by a small team. Keep it legible.

## What this is

`datalab-plot` plots electrochemistry / spectroscopy data pulled from a
[datalab](https://github.com/datalab-org/datalab) instance — locally, without
routing through the datalab server. Three surfaces:

- **Dash GUI** — `datalab-plot gui` (interactive browsing + plotting in a
  browser tab on `http://localhost:8050`).
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
├── plotting_panel.py Main dcc.Graph (stable mount) + Cycle Life tabs
├── export.py         CSV + Save / Load plot config (JSON)
├── plot_io.py        Pure save/load helpers for plot-config JSONs
└── assets/
    └── favicon.svg   Brand mark (also referenced by app.index_string)
```

### Layout pattern

Top-down stack of sections, each wrapped in `html.Div(className="ui-section")`
(hairline divider via CSS):

```
[Navbar: brand · cache-stats dropdown / Connect]
[Search]            ─ ui-section
[Search results]    ─ ui-section (AG Grid, ephemeral selection + Add to plot)
[Plotting]          ─ ui-section (AG Grid, durable, editable; default collapsed)
[Plot options]      ─ ui-section (preset + collapsible details + Refresh + Auto)
[Plot]              ─ ui-section (stable dcc.Graph; tabs for Cycle Life)
[Export]            ─ ui-section (PNG hint + CSV + Save/Load JSON)
```

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

- **Conditional widgets that get re-mounted on Input change can race.**
  `options._render_extras` swaps `opt-specific-capacity` and `opt-cycle`
  in/out based on mode. Writing to them in the same callback as
  `opt-mode.value` is not safe — the load-plot-config callback in
  `export.py` deliberately skips these two and leaves them at default.
  Documented limitation; not a bug.

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
- `#0083FF` bright blue — reserved (not currently used; avoid pairing
  with the navy primary)

Button hierarchy (single source of truth):
- **Solid primary** — modal Connect submit, navbar Connect (disconnected).
- **Outline primary** — Search, Refresh, Apply (×3 in staging), Save.
- **Outline secondary** — All / None / Invert, Cancel, Dismiss, Export
  CSV, Load, Delete, Remove selected.
- **Link with text-secondary** — collapse/expand chevrons (`▾ Items`,
  `▾ Plotting`, `▸ Plot options`, `⤢ Expand` / `⤡ Compact`).

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
datalab-plot gui            # Dash GUI at http://localhost:8050
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
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8050/
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8050/_dash-layout
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8050/_dash-dependencies
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
- Light/dark theme toggle. Bootstrap default light is the only theme.
