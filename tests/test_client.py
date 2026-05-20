"""Tests for cache resolution and the file-cache hit/miss logic.

These exercise ``DatalabPlotClient`` without any network: instances are built
with ``__new__`` so ``__init__`` (which constructs a real ``DatalabClient``)
never runs, and only the cache-hit path — which never touches the network —
is tested.
"""
from __future__ import annotations

from datalab_plot.cache import cache_dir
from datalab_plot.client import DatalabPlotClient


def test_cache_dir_honours_env(tmp_path, monkeypatch):
    target = tmp_path / "custom-cache"
    monkeypatch.setenv("DATALAB_PLOT_CACHE", str(target))
    resolved = cache_dir()
    assert resolved == target
    assert resolved.is_dir()  # created on resolution


def _client_with_cache(cache_root):
    c = DatalabPlotClient.__new__(DatalabPlotClient)
    c.cache_root = cache_root
    return c


def test_fetch_files_verbose_reports_cache_hit(tmp_path):
    c = _client_with_cache(tmp_path)
    item_dir = tmp_path / "ITEM-1"
    item_dir.mkdir()
    (item_dir / "data.csv").write_bytes(b"hello")  # 5 bytes
    item = {"files": [{"name": "data.csv", "size": 5, "immutable_id": "x"}]}

    results = c.fetch_files_verbose("ITEM-1", item=item)

    assert results == [(item_dir / "data.csv", "hit")]


def test_fetch_files_predicate_filters(tmp_path):
    c = _client_with_cache(tmp_path)
    item_dir = tmp_path / "ITEM-1"
    item_dir.mkdir()
    (item_dir / "keep.csv").write_bytes(b"hello")
    item = {
        "files": [
            {"name": "keep.csv", "size": 5, "immutable_id": "x"},
            {"name": "skip.png", "size": 5, "immutable_id": "y"},
        ]
    }

    results = c.fetch_files_verbose(
        "ITEM-1", lambda f: f["name"].endswith(".csv"), item=item
    )

    assert [p.name for p, _ in results] == ["keep.csv"]


def test_purge_removes_item_cache(tmp_path):
    c = _client_with_cache(tmp_path)
    item_dir = tmp_path / "ITEM-1"
    item_dir.mkdir()
    (item_dir / "f.csv").write_bytes(b"x")

    c.purge("ITEM-1")

    assert not item_dir.exists()
    # Purging a never-cached item is a no-op, not an error.
    c.purge("ITEM-NEVER")
