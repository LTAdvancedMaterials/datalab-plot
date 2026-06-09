"""Save / load plot configurations as JSON in the cache folder.

Pure I/O — no Dash, no Streamlit. Files live under ``cache_dir() /
"saved_plots/"``, alongside the per-item file caches that already use
``cache_dir()``.

A saved config captures everything needed to reconstruct a plot:
the staged set (which cells are plotted, with their label / group /
color) and the plot options (preset, mode, axes, styling, axis limits).
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datalab_plot.cache import cache_dir

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize_name(name: str) -> str:
    """Return a filesystem-safe filename stem from an arbitrary name.

    Replaces anything outside ``[a-zA-Z0-9_-]`` with ``_``; strips leading
    and trailing underscores. May return ``""`` if all chars were unsafe.
    """
    stem = _NAME_RE.sub("_", (name or "").strip())
    return stem.strip("_")


def _configs_dir() -> Path:
    """Return the ``saved_plots/`` directory, creating it lazily."""
    d = cache_dir() / "saved_plots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_plot_config(name: str, config: dict[str, Any]) -> Path:
    """Write ``config`` to ``<sanitized-name>.json``.

    Stamps ``name`` and ``saved_at`` on the config. Returns the file path.
    Raises ``ValueError`` if the sanitized name is empty.
    """
    stem = _sanitize_name(name)
    if not stem:
        raise ValueError("Plot name must contain at least one [a-zA-Z0-9_-] character")
    payload = dict(config)
    payload["name"] = name.strip()
    payload["saved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path = _configs_dir() / f"{stem}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_plot_config(name_or_stem: str) -> dict[str, Any]:
    """Read the saved config for ``name_or_stem`` (sanitized).

    Raises ``FileNotFoundError`` if no such file exists.
    """
    stem = _sanitize_name(name_or_stem)
    path = _configs_dir() / f"{stem}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Saved config {path} is not a JSON object")
    return data


def list_plot_configs() -> list[dict[str, Any]]:
    """Return summary metadata for every saved config, newest first.

    Each entry: ``{"stem": str, "name": str, "saved_at": str,
    "n_items": int}``. Corrupt files are silently skipped.
    """
    out: list[dict[str, Any]] = []
    for path in _configs_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        items = data.get("staged_items") or []
        out.append({
            "stem": path.stem,
            "name": data.get("name") or path.stem,
            "saved_at": data.get("saved_at") or "",
            "n_items": len(items) if isinstance(items, list) else 0,
        })
    out.sort(key=lambda e: e.get("saved_at", ""), reverse=True)
    return out


def delete_plot_config(name_or_stem: str) -> None:
    """Remove the saved config. No-op if the file doesn't exist."""
    stem = _sanitize_name(name_or_stem)
    if not stem:
        return
    path = _configs_dir() / f"{stem}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        pass
