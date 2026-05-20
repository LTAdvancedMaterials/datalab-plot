"""Notebook widget for picking cells interactively."""
from __future__ import annotations

from typing import Any

from .client import DatalabPlotClient, _resolve_client
from .search import find_cells


def pick_cells(
    query: str | None = None,
    *,
    item_type: str | tuple[str, ...] = ("samples", "cells"),
    limit: int = 200,
    client: DatalabPlotClient | None = None,
) -> Any:
    """Render an ipywidgets multi-select for items on the datalab instance.

    The returned object holds a live, theme-responsive widget. Highlighted rows
    *are* the selection -- click (or Cmd/Ctrl-click for multiple) to choose
    which cells will be plotted next time you call::

        plot_cycles(picker.selected)

    The widget below the list updates in real time so you can see exactly which
    cells the next plot call will pick up.
    """
    try:
        import ipywidgets as widgets
        from IPython.display import display
    except ImportError as exc:
        raise ImportError(
            "pick_cells requires ipywidgets. Install with: "
            "pip install 'datalab-plot[picker]'"
        ) from exc

    c, owns = _resolve_client(client)
    try:
        df = find_cells(query=query, item_type=item_type, limit=limit, client=c)
    finally:
        if owns:
            c.close()

    options: list[tuple[str, str]] = []
    for _, row in df.iterrows():
        bits = [row["item_id"]]
        if row["name"]:
            bits.append(row["name"])
        if row["chemform"]:
            bits.append(row["chemform"])
        label = "  ".join(b for b in bits if b)
        options.append((label, row["item_id"]))

    inv = {iid: label for label, iid in options}

    # Inject CSS so the native <select> obeys the JupyterLab theme. We use
    # descendant selectors (not '>'), `!important` to beat ipywidgets' own
    # styles, and a prefers-color-scheme media query as a final fallback for
    # classic Jupyter Notebook on a dark OS (which doesn't set --jp-* vars).
    style = widgets.HTML(
        value=(
            "<style>"
            ".datalab-plot-picker select,"
            ".datalab-plot-picker .widget-select-multiple select,"
            ".datalab-plot-picker .widget-select select {"
            "  background-color: var(--jp-layout-color1, transparent) !important;"
            "  color: var(--jp-content-font-color1, inherit) !important;"
            "  border: 1px solid var(--jp-border-color1, rgba(128,128,128,0.3)) !important;"
            "}"
            ".datalab-plot-picker select option:checked,"
            ".datalab-plot-picker .widget-select-multiple select option:checked,"
            ".datalab-plot-picker .widget-select select option:checked {"
            "  background-color: var(--jp-brand-color1, #2196f3) !important;"
            "  color: var(--jp-inverse-layout-color1, white) !important;"
            "}"
            "@media (prefers-color-scheme: dark) {"
            "  .datalab-plot-picker select,"
            "  .datalab-plot-picker .widget-select-multiple select,"
            "  .datalab-plot-picker .widget-select select {"
            "    background-color: var(--jp-layout-color1, #2a2a2a) !important;"
            "    color: var(--jp-content-font-color1, #e0e0e0) !important;"
            "  }"
            "}"
            "</style>"
        )
    )

    help_html = widgets.HTML(
        value=(
            "<div style='font-size:0.9em; opacity:0.75; margin-bottom:4px;'>"
            "Click a row to select. <kbd>Cmd</kbd>/<kbd>Ctrl</kbd>-click adds rows; "
            "<kbd>Shift</kbd>-click selects a range. "
            "Highlighted rows below are exactly what <code>picker.selected</code> "
            "returns &mdash; re-run your <code>plot_cycles(picker.selected)</code> "
            "cell after changing the selection."
            "</div>"
        )
    )

    select = widgets.SelectMultiple(
        options=options,
        rows=min(20, max(8, len(options))),
        description="",
        layout=widgets.Layout(width="100%", min_width="320px"),
    )

    def _status_html(sel: tuple) -> str:
        if not sel:
            return (
                "<div style='font-size:0.9em; opacity:0.75; margin-top:4px;'>"
                "<b>0</b> selected</div>"
            )
        # Single line, with overflow truncation so the row doesn't grow
        # unboundedly when many cells are picked.
        cap = 10
        head = ", ".join(f"<code>{iid}</code>" for iid in sel[:cap])
        extra = len(sel) - cap
        suffix = (
            "" if extra <= 0 else f" <span style='opacity:0.6;'>+{extra} more</span>"
        )
        return (
            "<div style='font-size:0.9em; margin-top:4px; "
            "white-space:nowrap; overflow:hidden; text-overflow:ellipsis;'>"
            f"<b>{len(sel)}</b> selected: {head}{suffix}"
            "</div>"
        )

    status = widgets.HTML(value=_status_html(()))

    def _refresh(_change=None) -> None:
        status.value = _status_html(select.value)

    select.observe(_refresh, names="value")

    box = widgets.VBox(
        [style, help_html, select, status],
        layout=widgets.Layout(width="100%"),
    )
    box.add_class("datalab-plot-picker")

    class _Picker:
        @property
        def selected(self) -> dict[str, str]:
            return {inv.get(iid, iid): iid for iid in select.value}

        @property
        def item_ids(self) -> list[str]:
            return list(select.value)

        def __repr__(self) -> str:
            return f"<pick_cells({len(select.value)} selected of {len(options)})>"

    picker = _Picker()
    display(box)
    return picker
