# Contributing

`datalab-plot` is a small internal tool that happens to be public. Patches are
welcome; expect a slow but real response.

## Setup

Python 3.12 only. Not 3.13: navani 0.1.x pins `numpy<2`, and numpy 1.26.4
publishes cp312 wheels only, so a newer interpreter forces a from-source numpy
build that fails on Windows without MSVC.

```sh
uv sync --all-extras      # runtime + gui + picker + dev tools
```

## Before you open a PR

```sh
make check                # ruff + mypy + pytest
```

GitHub Actions runs the same targets, so running it locally saves a round trip.
Individual targets are `make lint`, `make types`, `make test`, `make fmt`,
`make cov`.

`make check` deliberately does not run `ruff format --check`. Most of the tree
predates the formatter and reformatting it would bury real diffs. Format the
lines you touch, not the files.

### If you changed the Dash GUI

Nothing automated covers it. Boot the app and check the callback graph
serialises:

```sh
datalab-plot gui --no-browser
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_dash-layout
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_dash-dependencies
```

All three return `200`. A failure on `/_dash-dependencies` means a callback is
wired to a component id that is not in the layout, which no unit test catches.
CI runs this same check. For anything user-visible, open it in a browser too.

## Architecture

`CLAUDE.md` is the real guide: the module map, the data-flow rules, and the
reasons behind decisions that look strange from outside. Read it before moving
code between layers. The short version:

```
client.py / local_source.py   fetch items and files
        ↓
parsers/                      raw files → DataFrames        (pure, tested)
        ↓
series.py                     DataFrames → plot series      (pure, tested)
        ↓
plots/ (matplotlib)  |  plotly_builders.py (Plotly)         (rendering only)
        ↓
gui_dash/                     the only code importing dash
```

Data logic goes in `parsers/` or `series.py`. If you find yourself filtering
cycles or computing dQ/dV inside a renderer, it belongs one layer up where both
backends can call it.

## Tests

Synthetic in-memory fixtures only, in `tests/conftest.py`. Do not commit cycler
binaries, real cell data, or anything carrying a company name.

For a parser change, write the test so it fails against the old behaviour
first. A regression test that passed before your fix is testing the wrong
thing.

## Reporting a data bug

Numbers being wrong matters more here than anything else, and the failures look
plausible rather than obvious. If a capacity, coulombic efficiency or time axis
looks off, open an issue with the cycler and file format, what you expected,
what you got, and how you know the expected value. A synthetic fixture that
reproduces it is the most useful thing you can attach.

## Relationship with the upstream codebase

Three modules are backports from a private internal application. If you are
changing `parsers/echem.py`, `series.py` or `plots/echem.py`, read `SYNC.md`
first. Three things in them look like accidents and are not, and a cleanup that
"simplifies" any of them undoes a fix.
