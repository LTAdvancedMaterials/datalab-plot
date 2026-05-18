# datalab-plot

Plot electrochemistry data from a [datalab](https://docs.datalab-org.io) instance
locally — without going through the webapp. Multi-cell cycling comparisons,
single-cell deep dives, NMR / XRD / UV-Vis. Three user surfaces:

- **Streamlit GUI** — `datalab-plot gui` opens a local browser tab; search,
  multi-select cells, pick a plot mode, zoom and pan with Plotly.
- **Python API** — `from datalab_plot import plot_cycles, plot_cell, find_cells, …`
  returning `matplotlib.figure.Figure` for scripts and notebooks.
- **CLI** — `datalab-plot list / cycle / cell / nmr / xrd / uvvis` for one-shot
  PNG exports.

The library fetches the raw files via the datalab HTTP API, caches them locally
by size, and parses them with the same upstream libraries the datalab server
uses (`navani.echem`, `nmrglue`, …) — so the plots are produced entirely on
your machine and you can iterate offline once a file is cached.

## Install

```bash
# Core (Python API + CLI)
pip install git+https://github.com/ltadvancedmaterials/datalab-plot

# + interactive picker for Jupyter notebooks
pip install "datalab-plot[picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"

# + Streamlit web GUI
pip install "datalab-plot[gui] @ git+https://github.com/ltadvancedmaterials/datalab-plot"

# Everything
pip install "datalab-plot[gui,picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```

Or, with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install "datalab-plot[gui,picker] @ git+https://github.com/ltadvancedmaterials/datalab-plot"
```

Python 3.12+.

## Configuration

Two environment variables:

- `DATALAB_URL` — base URL of your instance (e.g. `https://datalab.lightningtree.ai/`).
- `DATALAB_API_KEY` — your personal API token. Generate one by signing into the
  instance in a browser and visiting `<DATALAB_URL>/get-api-key`.

Set them once per shell:

```bash
export DATALAB_URL=https://datalab.lightningtree.ai/
export DATALAB_API_KEY=...
```

The GUI prompts for the key interactively if it isn't already in the
environment; the library and CLI also read them at runtime.

## Web GUI

```bash
datalab-plot gui                    # opens http://localhost:8501
datalab-plot gui --port 9000        # custom port
datalab-plot gui --no-browser       # don't auto-open a browser tab
```

The flow in the app:

1. **Sidebar — Connect.** URL is pre-filled to `https://datalab.lightningtree.ai/`;
   paste your API key and click *Connect*. The sidebar shows ✓ and your
   instance name when connected.
2. **Search.** Type a free-text query (e.g. `NMC811`) and hit *Search*. Leave
   blank to list all items.
3. **Pick.** Tick rows in the table. Cmd/Ctrl-click multi-select works; **All
   / None / Invert / Range** buttons sit above the table for bulk actions.
   The inline `label`, `group`, and `color` columns let you rename cells and
   choose how they're coloured: cells sharing a `group` get the same
   perceptually uniform colormap; an explicit `color` overrides the
   auto-assignment.
4. **Plot.** Pick a mode (`voltage_time`, `summary`, `voltage_capacity`,
   `dqdv`) and — for `dqdv` only — a cycle number. With *Auto-plot* on
   (default), the plot live-updates as you tick rows. *Refresh from server*
   purges the local cache for the selected items and re-downloads.

If a cell's file can't be parsed (e.g. malformed CSV from a misbehaving cycler),
that row is auto-deselected and an error banner explains why.

## Python API

```python
import os
os.environ["DATALAB_URL"] = "https://datalab.lightningtree.ai/"
# os.environ["DATALAB_API_KEY"] = "..."   # or export beforehand

from datalab_plot import plot_cycles, plot_cell, find_cells, DatalabPlotClient

# Discover items
df = find_cells(query="NMC811", limit=50)        # → pandas DataFrame
print(df[["item_id", "name", "chemform"]])

# Multi-cell comparison
with DatalabPlotClient() as client:
    fig = plot_cycles(
        {
            "Pristine #1": {"item_id": "XXKSRF", "group": "Pristine"},
            "Pristine #2": {"item_id": "SJMSEL", "group": "Pristine"},
            "LiAlO2 #1":   {"item_id": "TNDKZB", "group": "LiAlO2"},
            "LiAlO2 #2":   {"item_id": "RQMFUG", "group": "LiAlO2"},
        },
        mode="summary",                          # discharge capacity + CE vs cycle
        client=client,
    )
    fig.savefig("comparison.png", dpi=140)

    # All cycles of one cell, V vs Q, coloured by cycle index (viridis)
    fig = plot_cell("XXKSRF", mode="voltage_capacity", client=client)
```

### Plot modes

| Mode                | `plot_cycles` (multi-cell)                          | `plot_cell` (single)                                  |
|---------------------|-----------------------------------------------------|-------------------------------------------------------|
| `summary`           | Discharge capacity + CE vs cycle                    | Same, one cell                                        |
| `voltage_time`      | V vs cumulative time, one trace per cell            | V vs time, all cycles                                 |
| `voltage_capacity`  | All cycles per cell, per-cell colormap              | V-Q with cycles coloured viridis                      |
| `dqdv`              | dQ/dV at chosen `cycle=N`, one trace per cell       | dQ/dV per chosen `cycles=[…]`, coloured by cycle      |

The `items` argument to `plot_cycles` accepts three shapes — bare list, `{label: item_id}` dict, or `{label: {item_id, group?, color?}}` — depending on how much control over the legend / colour scheme you want.

### Other plot types

Single-item, returning `matplotlib.figure.Figure`:

```python
from datalab_plot import plot_nmr, plot_xrd, plot_uvvis

plot_nmr("nmr_item_id")       # Bruker .zip or JCAMP-DX
plot_xrd("xrd_item_id")       # .xy, .xye, .dat, .xrdml
plot_uvvis("uvvis_item_id")   # .txt (first file = reference)
```

## CLI

```bash
datalab-plot list --query NMC811                                          # search
datalab-plot cycle XXKSRF SJMSEL TNDKZB --mode summary --out s.png        # multi-cell
datalab-plot cycle XXKSRF SJMSEL --mode dqdv --cycle 1 --out dqdv.png
datalab-plot cell XXKSRF --mode voltage_capacity --out cell.png
datalab-plot nmr <item_id> --out nmr.png
datalab-plot gui                                                           # launch web UI
```

`datalab-plot --help` and `datalab-plot <subcommand> --help` print everything.

## Jupyter

Open [`notebooks/starter.ipynb`](notebooks/starter.ipynb) — it walks through
URL/key setup, the interactive `pick_cells` widget, the four comparison plot
modes, and a custom-grouping example.

```bash
jupyter lab notebooks/starter.ipynb
```

## How it works

The datalab webapp renders every plot server-side as Bokeh JSON and just
embeds it in Vue. This library takes the orthogonal approach:

1. Download the raw uploaded files via `datalab_api.DatalabClient` and cache
   them locally (`~/.cache/datalab-plot/<item_id>/` for installed users, or
   `./cache/datalab_plot/` when run from a repo checkout). Size-checked: a
   cached file is reused when its byte count matches the server's metadata.
2. Parse with the same upstream libs the datalab server uses (`navani.echem`,
   `nmrglue`, custom XRD / UV-Vis parsers).
3. Draw fresh figures with matplotlib (library / CLI / notebook) or Plotly
   (GUI). No round-trip through the datalab plotting service.

## File formats supported

| Block       | Parser                         | File types                                                 |
|-------------|--------------------------------|------------------------------------------------------------|
| Cycle / echem | `navani.echem.echem_file_loader` | `.mpr`, `.res`, `.xls`, `.xlsx`, `.nda`, `.ndax`, `.csv`, `.txt` |
| NMR (1D)    | `nmrglue.bruker` / `nmrglue.jcampdx` | Bruker `.zip`, JCAMP-DX `.jdx` / `.dx`                     |
| XRD         | Custom parsers (`xml.etree`)   | `.xy`, `.xye`, `.dat`, `.xrdml`                            |
| UV-Vis      | Custom (pandas)                | `.txt` (semicolon-separated, 7-row header)                 |

Out of scope for v1: insitu blocks, EIS, CV, Raman, FTIR, TGA, CIF→PXRD,
Rasx, Bruker `.brml` / `.raw`. Add as needed.

## Development

```bash
git clone https://github.com/ltadvancedmaterials/datalab-plot
cd datalab-plot
uv sync --extra gui --extra picker     # or: pip install -e ".[gui,picker]"

# Run the GUI against an instance
export DATALAB_URL=https://datalab.lightningtree.ai/
export DATALAB_API_KEY=...
datalab-plot gui
```

Layout:

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
```

## License

TBD.
