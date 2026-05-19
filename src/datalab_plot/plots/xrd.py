"""matplotlib plot for XRD patterns."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from ..client import DatalabPlotClient, _resolve_client
from ..parsers.xrd import is_xrd_file, load_xrd


def plot_xrd(
    item_id: str,
    *,
    file_ids: list[str] | None = None,
    offset: float = 0.0,
    normalize: bool = False,
    client: DatalabPlotClient | None = None,
    ax: plt.Axes | None = None,
    title: str | None = None,
) -> Figure:
    """Plot one or more XRD patterns for an item.

    ``file_ids`` selects which files (immutable_ids) to overlay; defaults to all
    XRD files attached to the item. ``offset`` adds a vertical offset between
    successive traces. ``normalize=True`` scales each trace to its own maximum.
    """
    c, owns = _resolve_client(client)
    try:
        item = c.client.get_item(item_id=item_id)
        candidates = [f for f in item.get("files") or [] if is_xrd_file(f)]
        if file_ids:
            requested = set(file_ids)
            candidates = [f for f in candidates if f.get("immutable_id") in requested]
        if not candidates:
            raise RuntimeError(f"No XRD files found for item {item_id!r}")
        ids = {f.get("immutable_id") for f in candidates}
        paths = c.fetch_files(item_id, predicate=lambda f: f.get("immutable_id") in ids, item=item)
    finally:
        if owns:
            c.close()

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 5))
    else:
        fig = ax.figure

    for i, (f, path) in enumerate(zip(candidates, paths)):
        df = load_xrd(path)
        y = df["intensity"].astype(float)
        if normalize:
            ymax = y.max()
            if ymax:
                y = y / ymax
        if offset:
            y = y + i * offset
        ax.plot(df["twotheta"], y, lw=0.9, label=f["name"])

    ax.set_xlabel(r"$2\theta$ (degrees)")
    ax.set_ylabel("Intensity (normalised)" if normalize else "Intensity")
    ax.set_title(title or f"{item_id} — XRD")
    ax.grid(alpha=0.3)
    if len(candidates) > 1:
        ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    return fig
