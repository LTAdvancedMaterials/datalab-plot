"""matplotlib plot for UV-Vis absorbance."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from ..client import DatalabPlotClient, _resolve_client
from ..parsers.uvvis import find_absorbance, is_uvvis_file, parse_uvvis_txt


def plot_uvvis(
    item_id: str,
    *,
    reference_file_id: str | None = None,
    client: DatalabPlotClient | None = None,
    ax: plt.Axes | None = None,
    title: str | None = None,
) -> Figure:
    """Plot UV-Vis absorbance (wavelength vs absorbance) for the files attached to an item.

    The first file alphabetically (or the one named by ``reference_file_id``) is
    used as the reference; subsequent files are plotted as ``-log10(I/I_ref)``.
    """
    c, owns = _resolve_client(client)
    try:
        item = c.client.get_item(item_id=item_id)
        candidates = sorted(
            (f for f in item.get("files") or [] if is_uvvis_file(f)),
            key=lambda f: f["name"],
        )
        if len(candidates) < 2:
            raise RuntimeError(
                f"Need at least 2 .txt files for UV-Vis (one reference + one sample); "
                f"item {item_id!r} has {len(candidates)}"
            )
        if reference_file_id is None:
            ref = candidates[0]
        else:
            ref = next((f for f in candidates if f.get("immutable_id") == reference_file_id), None)
            if ref is None:
                raise ValueError(
                    f"reference_file_id {reference_file_id!r} not among UV-Vis files"
                )
        samples = [f for f in candidates if f.get("immutable_id") != ref.get("immutable_id")]
        ids = {f["immutable_id"] for f in candidates}
        paths = c.fetch_files(
            item_id, predicate=lambda f: f.get("immutable_id") in ids, item=item
        )
    finally:
        if owns:
            c.close()

    name_to_path = {p.name: p for p in paths}
    ref_df = parse_uvvis_txt(name_to_path[ref["name"]])

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 5))
    else:
        # ax belongs to a top-level Figure (never a SubFigure) in this app.
        fig = ax.figure  # type: ignore[assignment]

    for s in samples:
        sample_df = parse_uvvis_txt(name_to_path[s["name"]])
        abs_df = find_absorbance(sample_df, ref_df)
        ax.plot(abs_df["Wavelength"], abs_df["Absorbance"], lw=1.0, label=s["name"])

    ax.set_xlabel("Wavelength (nm)")
    ax.set_ylabel("Absorbance")
    ax.set_title(title or f"{item_id} — UV-Vis (ref: {ref['name']})")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    return fig
