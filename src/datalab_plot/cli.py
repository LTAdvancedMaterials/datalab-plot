"""``datalab-plot`` command-line entry point."""
from __future__ import annotations

import argparse
import sys

import matplotlib

from .client import DatalabPlotClient
from .plots.echem import plot_cell, plot_cycles
from .plots.nmr import plot_nmr
from .plots.uvvis import plot_uvvis
from .plots.xrd import plot_xrd
from .search import find_cells


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datalab-plot",
        description=(
            "Plot data from a datalab instance locally. "
            "Set DATALAB_URL in your environment."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List or search items on the datalab instance")
    p_list.add_argument("--query", "-q", default=None)
    p_list.add_argument("--type", dest="item_type", default="samples,cells")
    p_list.add_argument("--limit", type=int, default=50)

    p_cycle = sub.add_parser("cycle", help="Multi-cell cycling comparison")
    p_cycle.add_argument("item_ids", nargs="+")
    p_cycle.add_argument(
        "--mode",
        choices=["summary", "voltage_capacity", "dqdv", "voltage_time"],
        default="summary",
    )
    p_cycle.add_argument("--cycle", type=int, default=None)
    p_cycle.add_argument("--out", default="plot.png")
    p_cycle.add_argument("--title", default=None)

    p_cell = sub.add_parser("cell", help="Single-cell deep dive")
    p_cell.add_argument("item_id")
    p_cell.add_argument(
        "--mode",
        choices=["voltage_time", "voltage_capacity", "dqdv", "summary"],
        default="voltage_time",
    )
    p_cell.add_argument("--cycles", default=None, help="Comma-separated cycle numbers")
    p_cell.add_argument("--out", default="plot.png")

    for kind in ("nmr", "xrd", "uvvis"):
        p = sub.add_parser(kind, help=f"Plot a single {kind.upper()} item")
        p.add_argument("item_id")
        p.add_argument("--out", default="plot.png")

    p_gui = sub.add_parser("gui", help="Launch the Streamlit GUI in a browser")
    p_gui.add_argument("--port", type=int, default=8501)
    p_gui.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the server but don't open a browser tab automatically",
    )

    return parser


def _cmd_list(args: argparse.Namespace) -> int:
    types = tuple(t.strip() for t in args.item_type.split(",") if t.strip())
    # enrich=False: `list` prints only summary columns, so skip the
    # per-cell full-document fetches that fill electrode / electrolyte.
    df = find_cells(query=args.query, item_type=types, limit=args.limit, enrich=False)
    if df.empty:
        print("(no items)", file=sys.stderr)
        return 0
    cols = ["item_id", "name", "type", "chemform", "last_modified"]
    cols = [c for c in cols if c in df.columns]
    widths = {c: max(len(c), df[c].astype(str).str.len().max()) for c in cols}
    print("  ".join(f"{c:<{widths[c]}}" for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for _, row in df.iterrows():
        print("  ".join(f"{str(row[c]):<{widths[c]}}" for c in cols))
    return 0


def _save(fig, out: str) -> None:
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"Saved {out}")


def _cmd_cycle(args: argparse.Namespace) -> int:
    matplotlib.use("Agg")
    with DatalabPlotClient() as c:
        fig = plot_cycles(
            args.item_ids,
            mode=args.mode,
            cycle=args.cycle,
            client=c,
            title=args.title,
        )
    _save(fig, args.out)
    return 0


def _cmd_cell(args: argparse.Namespace) -> int:
    matplotlib.use("Agg")
    cycles = None
    if args.cycles:
        cycles = [int(x) for x in args.cycles.split(",")]
    with DatalabPlotClient() as c:
        fig = plot_cell(args.item_id, mode=args.mode, cycles=cycles, client=c)
    _save(fig, args.out)
    return 0


def _cmd_simple(plot_fn):
    def _run(args: argparse.Namespace) -> int:
        matplotlib.use("Agg")
        with DatalabPlotClient() as c:
            fig = plot_fn(args.item_id, client=c)
        _save(fig, args.out)
        return 0

    return _run


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        from streamlit.web import bootstrap
    except ImportError:
        print(
            "Streamlit is not installed. Install with: pip install 'datalab-plot[gui]'",
            file=sys.stderr,
        )
        return 1
    from pathlib import Path

    script = str(Path(__file__).parent / "gui" / "app.py")
    flag_options = {
        "server.port": args.port,
        "server.headless": args.no_browser,
        "browser.gatherUsageStats": False,
    }
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(script, is_hello=False, args=[], flag_options=flag_options)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dispatch = {
        "list": _cmd_list,
        "cycle": _cmd_cycle,
        "cell": _cmd_cell,
        "nmr": _cmd_simple(plot_nmr),
        "xrd": _cmd_simple(plot_xrd),
        "uvvis": _cmd_simple(plot_uvvis),
        "gui": _cmd_gui,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
