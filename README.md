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

You'll need a datalab instance URL and a personal API token. Get the token by
signing into the instance in a browser and visiting `<DATALAB_URL>/get-api-key`.

### 1 · Install

Pick whichever fits — both give you the `datalab-plot` command, the Python
library, and the GUI. Python 3.12+ required either way.

**Option A — clone the repo** (recommended if you want the starter notebook
to hand or might tweak the code):

```bash
git clone https://github.com/ltadvancedmaterials/datalab-plot
cd datalab-plot
uv sync --extra gui --extra picker         # or: pip install -e ".[gui,picker]"
```

**Option B — install without cloning** (recommended for everyone else):

```bash
pip install "datalab-plot[gui,picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```
or with uv:
```bash
uv pip install "datalab-plot[gui,picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```

Optional extras: `[gui]` for the Streamlit web UI, `[picker]` for the
ipywidgets cell-picker in Jupyter. The core package works without either.

### 2 · Launch the web UI

```bash
datalab-plot gui                 # opens http://localhost:8501
```

The web UI **does not require any environment variables** — the URL field is
pre-filled to `https://datalab.lightningtree.ai/` and the API key has a
password input in the sidebar. Paste your key → **Connect** → search for
items (e.g. `NMC811`) → tick rows → pick a plot mode. Plot auto-updates
as you tick / untick rows.

### 3 · …or use it from Python

For the Python API, CLI, and notebook, export the credentials once per shell:

```bash
export DATALAB_URL=https://datalab.lightningtree.ai/
export DATALAB_API_KEY=...
```

Then:

```python
from datalab_plot import plot_cycles, find_cells, DatalabPlotClient

# Browse what's on the instance
print(find_cells(query="NMC811", limit=20)[["item_id", "name", "chemform"]])

# Multi-cell comparison: discharge capacity + CE vs cycle
with DatalabPlotClient() as client:
    fig = plot_cycles(
        {
            "Pristine #1": {"item_id": "XXKSRF", "group": "Pristine"},
            "Pristine #2": {"item_id": "SJMSEL", "group": "Pristine"},
            "LiAlO2 #1":   {"item_id": "TNDKZB", "group": "LiAlO2"},
            "LiAlO2 #2":   {"item_id": "RQMFUG", "group": "LiAlO2"},
        },
        mode="summary",
        client=client,
    )
    fig.savefig("comparison.png", dpi=140)
```

### 4 · …or open the starter notebook

```bash
jupyter lab notebooks/starter.ipynb
```

The notebook walks through URL/key setup, an interactive `pick_cells` widget,
all four cycling plot modes, and a custom-grouping example.

---

## Configuration

| Env var            | What it is                                    |
|--------------------|-----------------------------------------------|
| `DATALAB_URL`      | Base URL of your instance                     |
| `DATALAB_API_KEY`  | Personal API token, from `<URL>/get-api-key`  |

Both are read at runtime. The GUI prompts for the key interactively if it
isn't in the environment; the library + CLI raise a clear error if either
is missing.

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
