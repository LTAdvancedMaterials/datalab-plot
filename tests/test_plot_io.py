"""Tests for plot-config save/list metadata + the source-hint formatter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from datalab_plot.gui_dash import plot_io
from datalab_plot.gui_dash.plot_io import (
    list_plot_configs,
    save_plot_config,
    source_hint,
)


@pytest.fixture()
def configs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "saved_plots"
    d.mkdir()
    monkeypatch.setattr(plot_io, "_configs_dir", lambda: d)
    return d


def test_source_hint_local() -> None:
    assert source_hint("local:/data/cells") == "📁 cells"
    assert source_hint("local:") == "📁 local"


def test_source_hint_url() -> None:
    assert source_hint("https://datalab.example.com/") == "datalab.example.com"
    assert source_hint("http://localhost:5001") == "localhost"


def test_source_hint_empty_or_unknown() -> None:
    assert source_hint("") == ""
    assert source_hint(None) == ""  # type: ignore[arg-type]
    assert source_hint("ftp://weird") == ""


def test_list_plot_configs_includes_source(configs_dir: Path) -> None:
    save_plot_config(
        "local-one",
        {"datalab_url": "local:/data/cells", "staged_items": [{"item_id": "a"}]},
    )
    save_plot_config(
        "server-one",
        {"datalab_url": "https://dl.example.com/", "staged_items": []},
    )
    # Pre-feature config without the field.
    (configs_dir / "old.json").write_text(
        json.dumps({"name": "old", "staged_items": []}), encoding="utf-8"
    )

    entries = {e["name"]: e for e in list_plot_configs()}
    assert entries["local-one"]["source"] == "local:/data/cells"
    assert entries["server-one"]["source"] == "https://dl.example.com/"
    assert entries["old"]["source"] == ""
    assert entries["local-one"]["n_items"] == 1
