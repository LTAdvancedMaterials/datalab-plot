# datalab-plot

Plot electrochemistry data from a [datalab](https://docs.datalab-org.io) instance
locally — without going through the webapp. Multi-cell cycling comparisons,
single-cell deep dives, NMR / XRD / UV-Vis. Three user surfaces:

- **Streamlit web UI** — `datalab-plot gui` opens a browser tab; search,
  multi-select cells, pick a plot mode, zoom and pan with Plotly.
- **Python API** — `from datalab_plot import plot_cycles, plot_cell, find_cells, …`
  returning `matplotlib.figure.Figure` for scripts and notebooks.
- **CLI** — `datalab-plot list / cycle / cell / nmr / xrd / uvvis` for one-shot
  PNG exports.

## Quickstart

Three commands from a cold machine to the web UI. Works on macOS, Linux,
and Windows. You'll need a datalab instance URL and a personal API token
(get one at `<DATALAB_URL>/get-api-key`).

### 1 · Install `uv`

[`uv`](https://docs.astral.sh/uv/) fetches the right Python for you — no
separate Python install needed.

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows PowerShell:**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart your terminal so `uv` is on `PATH`. Already have `uv`? Skip ahead.

### 2 · Install `datalab-plot`

From any directory you want the project's environment to live in (e.g.
your home dir). The two commands below are identical on all OSes:

```bash
uv venv --python 3.12
uv pip install "datalab-plot[gui,picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```

`uv` downloads Python 3.12 if you don't already have it, creates `.venv/`
next to you, and installs `datalab-plot` plus the GUI/Jupyter extras.

### 3 · Launch the web UI

```bash
uv run datalab-plot gui
```

`uv run` finds the `.venv` automatically — no activation needed. Your
browser opens to `http://localhost:8501`. Paste your API key in the
sidebar → **Connect** → search → tick rows → pick a plot mode.

> Run subsequent commands from the same directory so `uv run` finds the
> venv, or activate it once with `source .venv/bin/activate`
> (macOS/Linux) or `.venv\Scripts\Activate.ps1` (Windows) and drop the
> `uv run` prefix.

See [Install](#install) below for the repo-clone setup, `pip`-only
install, and troubleshooting.

---

## Configuration

| Env var            | What it is                                    |
|--------------------|-----------------------------------------------|
| `DATALAB_URL`      | Base URL of your instance                     |
| `DATALAB_API_KEY`  | Personal API token, from `<URL>/get-api-key`  |

Both are read at runtime. The GUI prompts for the key interactively if it
isn't in the environment; the library + CLI raise a clear error if either
is missing.

The GUI also **remembers the key** after a successful connection: it
saves it per-instance-URL to `credentials.json` in the platform
user-config directory (owner-only file permissions), so you don't have
to paste it in every restart. Use *Forget saved key* in the sidebar's
Connection panel to clear it.

`DATALAB_PLOT_CACHE` (optional) overrides the local file cache directory.
Default: `./cache/datalab_plot/` when run from a repo checkout, otherwise
the platform user cache (e.g. `~/.cache/datalab-plot/` on Linux).

## Web GUI

```bash
datalab-plot gui                  # http://localhost:8501
datalab-plot gui --port 9000      # custom port
datalab-plot gui --no-browser     # start server without opening a tab
```

The single-page flow:

1. **Sidebar — Connect.** URL is pre-filled to `https://datalab.lightningtree.ai/`;
   paste your API key and hit *Connect*. Status flips to ✓.
2. **Search.** Free-text query (e.g. `NMC811`) or leave blank to list
   everything.
3. **Pick.** Tick rows. **All / None / Invert / Range** buttons sit above the
   table for bulk actions. The inline `label`, `group`, and `color` columns
   let you rename cells and choose how they're coloured — cells sharing a
   `group` get the same perceptually uniform colormap.
4. **Plot.** Pick a mode (`voltage_time`, `summary`, `voltage_capacity`,
   `dqdv`) and — for `dqdv` only — a cycle number. With *Auto-plot* on
   (default), the plot live-updates whenever the selection changes.
   *Refresh from server* purges the local cache for selected items and
   re-downloads.

If a cell's file can't be parsed (e.g. malformed cycler export), that row
is auto-deselected and an error banner explains why.

## Python API

### Discovery

```python
from datalab_plot import find_cells

df = find_cells(query="NMC811", limit=50)        # → pandas DataFrame
# columns: item_id, name, refcode, type, chemform, last_modified, collections
```

### Multi-cell comparison

```python
from datalab_plot import plot_cycles, DatalabPlotClient

with DatalabPlotClient() as client:
    fig = plot_cycles(
        ["XXKSRF", "SJMSEL", "TNDKZB"],          # or a {label: item_id} dict
        mode="summary",
        client=client,
    )

    # All cycles per cell, each cell a different perceptually uniform colormap
    fig = plot_cycles(["XXKSRF", "TNDKZB"], mode="voltage_capacity", client=client)

    # dQ/dV overlay at a specific cycle
    fig = plot_cycles(["XXKSRF", "TNDKZB"], mode="dqdv", cycle=1, client=client)

    # V vs cumulative time
    fig = plot_cycles(["XXKSRF", "TNDKZB"], mode="voltage_time", client=client)
```

The `items` argument accepts three shapes — bare list, `{label: item_id}`
dict, or `{label: {item_id, group?, color?}}` — depending on how much
control over the legend / colour scheme you want.

### Single cell

```python
from datalab_plot import plot_cell

plot_cell("XXKSRF", mode="voltage_capacity")     # V-Q, cycles coloured viridis
plot_cell("XXKSRF", mode="voltage_time")
plot_cell("XXKSRF", mode="dqdv", cycles=[1, 5, 20])
plot_cell("XXKSRF", mode="summary")
```

### Other techniques

```python
from datalab_plot import plot_nmr, plot_xrd, plot_uvvis

plot_nmr("nmr_item_id")          # Bruker .zip or JCAMP-DX
plot_xrd("xrd_item_id")          # .xy, .xye, .dat, .xrdml
plot_uvvis("uvvis_item_id")      # .txt (first file = reference)
```

### Plot-mode summary

| Mode               | `plot_cycles` (multi-cell)                          | `plot_cell` (single)                              |
|--------------------|-----------------------------------------------------|---------------------------------------------------|
| `summary`          | Discharge capacity + CE vs cycle                    | Same, one cell                                    |
| `voltage_time`     | V vs cumulative time, one trace per cell            | V vs time, all cycles                             |
| `voltage_capacity` | All cycles per cell, per-cell colormap              | V-Q with cycles coloured viridis                  |
| `dqdv`             | dQ/dV at chosen `cycle=N`, one trace per cell       | dQ/dV per chosen `cycles=[…]`, coloured by cycle  |

## CLI

```bash
datalab-plot list --query NMC811
datalab-plot cycle XXKSRF SJMSEL TNDKZB --mode summary --out s.png
datalab-plot cycle XXKSRF SJMSEL --mode dqdv --cycle 1 --out dqdv.png
datalab-plot cell XXKSRF --mode voltage_capacity --out cell.png
datalab-plot nmr <item_id> --out nmr.png
datalab-plot gui
```

`datalab-plot --help` and `datalab-plot <subcommand> --help` print everything.

## Install

The [Quickstart](#quickstart) covers the common path (`uv` + no clone +
GUI). The variants below are for everyone else.

### Clone the repo

Pick this if you want the starter notebook to hand or might tweak the
code.

```bash
git clone https://github.com/ltadvancedmaterials/datalab-plot
cd datalab-plot
uv sync --extra gui --extra picker
```

`uv sync` creates `.venv/` in the repo on Python 3.12 and installs
everything from `pyproject.toml`. Activate the venv
(`source .venv/bin/activate` / `.venv\Scripts\Activate.ps1`) or prefix
commands with `uv run`.

### Without `uv` (plain `pip`)

You must be on Python 3.12 — see [why](#why-pin-python-312) below.

```bash
python3.12 -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
.venv\Scripts\Activate.ps1

pip install "datalab-plot[gui,picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```

### Optional extras

| Extra      | Pulls in                  | Needed for                                |
|------------|---------------------------|-------------------------------------------|
| `[gui]`    | Streamlit, Plotly         | `datalab-plot gui` web UI                 |
| `[picker]` | ipywidgets                | Interactive `pick_cells` in Jupyter       |

Core library + CLI work without either:

```bash
uv pip install "datalab-plot @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```

### Why pin Python 3.12?

An upstream dependency (`navani`) pins `numpy<2`, and numpy 1.26 only
publishes binary wheels for cp312. On Python 3.13+ the resolver still
picks numpy 1.26 and falls back to building it from source, which needs
a C/C++ toolchain (MSVC on Windows) — painful and slow. Sticking to 3.12
sidesteps this entirely. We'll lift the cap once `navani` releases a
numpy-2 compatible version.

### Troubleshooting

<details>
<summary><code>Failed to build numpy==1.26.4</code> / "Unknown compiler" on Windows</summary>

Your interpreter is Python 3.13 or newer. Re-create the venv on 3.12:

```powershell
Remove-Item -Recurse -Force .venv
uv venv --python 3.12
uv pip install "datalab-plot[gui,picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```

</details>

<details>
<summary><code>datalab-plot: command not found</code></summary>

Either your `.venv` isn't activated and you're not using `uv run`, or
you're in a different directory than the one you installed into. Either
re-run from the install directory with `uv run datalab-plot ...`, or
activate the venv (`source .venv/bin/activate` /
`.venv\Scripts\Activate.ps1`) and try again.

</details>

## How it works

The datalab webapp renders every plot server-side as Bokeh JSON and just
embeds it in Vue. This library takes the orthogonal approach:

1. Download the raw uploaded files via `datalab_api.DatalabClient` and cache
   them locally, size-checked so a cached file is reused when its byte
   count matches the server's metadata.
2. Parse with the same upstream libraries the datalab server uses
   (`navani.echem`, `nmrglue`, custom XRD / UV-Vis parsers).
3. Draw fresh figures with matplotlib (library / CLI / notebook) or Plotly
   (GUI). No round-trip through the datalab plotting service.

### File formats supported

| Block         | Parser                                  | File types                                                       |
|---------------|-----------------------------------------|------------------------------------------------------------------|
| Cycle / echem | `navani.echem.echem_file_loader`        | `.mpr`, `.res`, `.xls`, `.xlsx`, `.nda`, `.ndax`, `.csv`, `.txt` |
| NMR (1D)      | `nmrglue.bruker` / `nmrglue.jcampdx`    | Bruker `.zip`, JCAMP-DX `.jdx` / `.dx`                           |
| XRD           | Custom parsers (`xml.etree`)            | `.xy`, `.xye`, `.dat`, `.xrdml`                                  |
| UV-Vis        | Custom (pandas)                         | `.txt` (semicolon-separated, 7-row header)                       |

Out of scope for v1: insitu blocks, EIS, CV, Raman, FTIR, TGA, CIF→PXRD,
Rasx, Bruker `.brml` / `.raw`. Add as needed.

## Repository layout

```
src/datalab_plot/
  client.py            DatalabPlotClient: size-checked file cache
  cache.py             Cache directory resolution
  search.py            find_cells (search / list)
  picker.py            ipywidgets multi-select for Jupyter
  cli.py               argparse entry point (incl. `gui` subcommand)
  gui.py               Streamlit app (Plotly figures)
  parsers/
    echem.py           Navani wrapper + dQ/dV + cycle-split helpers
    nmr.py             Bruker + JCAMP
    xrd.py             XRDML + whitespace-delimited
    uvvis.py           ASCII export parsing
  plots/
    echem.py           plot_cycles + plot_cell (matplotlib)
    nmr.py, xrd.py, uvvis.py
notebooks/
  starter.ipynb        End-to-end demo notebook
```

## License

TBD.
