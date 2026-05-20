# CLAUDE.md — agent guide for datalab-plot

Project context for AI agents working in this repo. This codebase is written
entirely by agents, for internal use by a small team. Keep it legible.

## What this is

`datalab-plot` plots electrochemistry / spectroscopy data pulled from a
[datalab](https://github.com/datalab-org/datalab) instance — locally, without
routing through the datalab server. Three surfaces:

- **Streamlit GUI** — `datalab-plot gui` (interactive browsing + plotting).
- **Python API** — `plot_cycles`, `plot_cell`, `find_cells` (matplotlib figures).
- **CLI** — `datalab-plot ...` (one-shot PNG export).

## Architecture

Data flows in one direction — keep it that way:

```
client.py / search.py   fetch items + files from datalab (with a local cache)
        │
        ▼
parsers/   (echem, nmr, xrd, uvvis)   raw files -> pandas DataFrames
        │
        ▼
series.py   DataFrames -> render-agnostic plot series (NamedTuples)
        │
        ├─► plots/   matplotlib rendering (Python API + CLI)
        └─► gui/     Plotly rendering (Streamlit GUI)
```

- `parsers/` and `series.py` are **pure** (no Streamlit, no I/O) — this is the
  tested layer. Put new data logic here, not in `gui/` or `plots/`.
- `gui/` and `plots/` are **rendering only**. They must not re-implement data
  transforms — if you find yourself filtering cycles or computing dQ/dV in a
  renderer, move it to `series.py` and have both backends call it.
- No circular imports. Dependency direction is strictly top-to-bottom above.

Key modules: `client.py` (cached datalab file downloads), `search.py` (item
discovery), `cache.py` (cache-dir resolution), `cli.py` (CLI + launches the
Streamlit app), `picker.py` (ipywidgets selector for Jupyter).

## Conventions

- Python **3.12 only** (`requires-python = ">=3.12,<3.13"`). This is forced by
  `navani` pinning `numpy<2`; numpy 1.26 only ships cp312 wheels. Do not bump.
- All modules start with `from __future__ import annotations`.
- Use `logging.getLogger(__name__)` — never `print` (except in `cli.py`, where
  stdout/stderr is the user interface). Log caught exceptions; don't swallow
  them silently.
- GUI state lives in `st.session_state`. **Never rename a session-state key
  string** without auditing every use — the picker's version-key trick and
  auto-connect logic depend on exact key names.
- Tests use **synthetic in-memory data only** (see `tests/conftest.py`). Do not
  commit real company data files.

## Working in this repo

Dev tools are in the `dev` dependency group. Setup:

```sh
uv sync --all-extras      # installs runtime + gui/picker extras + dev tools
```

Run the full check loop before committing (there is no CI):

```sh
make check                # ruff lint + mypy + pytest  -- must pass
```

Individual targets: `make lint`, `make types`, `make test`, `make fmt`
(format), `make cov` (coverage). Raw equivalents: `uv run ruff check src tests`,
`uv run mypy`, `uv run pytest`.

Run the app / CLI:

```sh
datalab-plot gui          # Streamlit GUI at http://localhost:8501
datalab-plot --help       # CLI
```

Credentials: `DATALAB_URL` / `DATALAB_API_KEY` via env or `.env` (see
`.env.example`). The GUI also persists API keys per-URL under the user config
dir.
