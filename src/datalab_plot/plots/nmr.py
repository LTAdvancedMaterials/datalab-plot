"""matplotlib plot for 1D NMR spectra."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from ..client import DatalabPlotClient, _resolve_client
from ..parsers.nmr import is_nmr_file, load_nmr


def plot_nmr(
    item_id: str,
    *,
    file_id: str | None = None,
    normalize: bool = False,
    client: DatalabPlotClient | None = None,
    ax: plt.Axes | None = None,
    title: str | None = None,
) -> Figure:
    """Plot a 1D NMR spectrum (chemical shift vs intensity).

    If the item has multiple NMR files, pass ``file_id`` (the file's
    ``immutable_id``) to choose one; otherwise the first matching file is used.
    """
    c, owns = _resolve_client(client)
    try:
        item = c.client.get_item(item_id=item_id)
        candidates = [f for f in item.get("files") or [] if is_nmr_file(f)]
        if not candidates:
            raise RuntimeError(f"No NMR files found for item {item_id!r}")
        if file_id is None:
            target = candidates[0]
        else:
            target = next((f for f in candidates if f.get("immutable_id") == file_id), None)
            if target is None:
                names = [f["name"] for f in candidates]
                raise ValueError(
                    f"file_id {file_id!r} not found among NMR files; available: {names}"
                )
        paths = c.fetch_files(
            item_id,
            predicate=lambda f: f.get("immutable_id") == target.get("immutable_id"),
            item=item,
        )
    finally:
        if owns:
            c.close()

    df, meta = load_nmr(paths[0])

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 5))
    else:
        fig = ax.figure

    y = df["intensity_per_scan"] if normalize else df["intensity"]
    ax.plot(df["ppm"], y, lw=0.8)
    ax.invert_xaxis()
    ax.set_xlabel("Chemical shift (ppm)")
    ax.set_ylabel("Intensity" + (" / scan" if normalize else ""))
    ax.set_title(title or f"{item_id} — {meta.get('title') or target['name']}".strip())
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig
