"""Tests for the local-folder data source (no parsing — listing/safety only)."""
from __future__ import annotations

from pathlib import Path

import pytest

from datalab_plot.local_source import LocalFolderSource, connect_local
from datalab_plot.parsers.echem import is_cycling_file
from datalab_plot.picker_helpers import _build_initial_df


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """A folder with a mix of cycling files, noise, and a subfolder."""
    (tmp_path / "cellA.mpr").write_bytes(b"\x00" * 8)
    (tmp_path / "cellB.ndax").write_bytes(b"\x00" * 8)
    (tmp_path / "notes.md").write_text("not a cycler file")
    sub = tmp_path / "batch2"
    sub.mkdir()
    (sub / "cellC.nda").write_bytes(b"\x00" * 8)
    (sub / "cellA.mpr").write_bytes(b"\x00" * 8)  # same name, different dir
    return tmp_path


def test_scan_finds_nested_cycling_files_only(data_dir: Path) -> None:
    src = LocalFolderSource(data_dir)
    df = src.list_files()
    ids = set(df["item_id"])
    assert ids == {"cellA.mpr", "cellB.ndax", "batch2/cellC.nda", "batch2/cellA.mpr"}
    # Non-cycling files are excluded.
    assert "notes.md" not in ids


def test_query_filters_relative_path_case_insensitively(data_dir: Path) -> None:
    src = LocalFolderSource(data_dir)
    assert set(src.list_files("BATCH2")["item_id"]) == {
        "batch2/cellC.nda",
        "batch2/cellA.mpr",
    }
    assert set(src.list_files("cella")["item_id"]) == {
        "cellA.mpr",
        "batch2/cellA.mpr",
    }
    assert src.list_files("no-such-file").empty


def test_item_id_is_posix_relative_and_name_is_filename(data_dir: Path) -> None:
    src = LocalFolderSource(data_dir)
    df = src.list_files()
    row = df[df["item_id"] == "batch2/cellC.nda"].iloc[0]
    assert "/" in row["item_id"] and "\\" not in row["item_id"]
    assert row["name"] == "cellC.nda"
    assert row["cathode_mass_mg"] is None


def test_fetch_files_verbose_returns_hit(data_dir: Path) -> None:
    src = LocalFolderSource(data_dir)
    results = src.fetch_files_verbose("batch2/cellC.nda", predicate=is_cycling_file)
    assert len(results) == 1
    path, status = results[0]
    assert status == "hit"
    assert path == (data_dir / "batch2" / "cellC.nda").resolve()


def test_fetch_files_verbose_rejects_escape_and_missing(data_dir: Path) -> None:
    src = LocalFolderSource(data_dir)
    # Escaping the root via .. must yield nothing, even if the target exists.
    outside = data_dir.parent / "outside.mpr"
    outside.write_bytes(b"\x00")
    assert src.fetch_files_verbose("../outside.mpr") == []
    assert src.fetch_files_verbose("does-not-exist.mpr") == []


def test_purge_never_touches_user_files(data_dir: Path) -> None:
    src = LocalFolderSource(data_dir)
    target = data_dir / "cellA.mpr"
    before = target.read_bytes()
    src.purge("cellA.mpr")
    assert target.exists()
    assert target.read_bytes() == before


def test_cache_root_is_not_the_data_folder(data_dir: Path) -> None:
    # SAFETY: rmtree-style cache logic walks cache_root; it must never
    # point at the user's data.
    src = LocalFolderSource(data_dir)
    assert Path(src.cache_root).resolve() != src.root


def test_get_item_returns_empty_metadata(data_dir: Path) -> None:
    assert LocalFolderSource(data_dir).get_item("cellA.mpr") == {}


def test_connect_local_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Enter a folder path"):
        connect_local("")
    with pytest.raises(ValueError, match="Folder not found"):
        connect_local(str(tmp_path / "nope"))
    f = tmp_path / "file.mpr"
    f.write_bytes(b"\x00")
    with pytest.raises(ValueError, match="Not a folder"):
        connect_local(str(f))
    src = connect_local(str(tmp_path))
    assert src.root == tmp_path.resolve()


def test_list_files_feeds_picker_builder(data_dir: Path) -> None:
    """The find_cells-shaped frame flows through _build_initial_df."""
    src = LocalFolderSource(data_dir)
    picker_df = _build_initial_df(src.list_files(), None)
    assert len(picker_df) == 4
    assert "Select" in picker_df.columns
    # Default label falls back to the file name.
    row = picker_df[picker_df["item_id"] == "cellB.ndax"].iloc[0]
    assert row["label"] == "cellB.ndax"
