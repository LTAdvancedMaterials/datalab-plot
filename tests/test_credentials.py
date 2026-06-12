"""Tests for the credentials store's last-local-dir persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from datalab_plot import credentials


@pytest.fixture()
def creds_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(credentials, "creds_path", lambda: path)
    return path


def test_last_local_dir_round_trip(creds_file: Path) -> None:
    assert credentials.load_creds()["last_local_dir"] == ""
    credentials.save_local_dir("/data/cells")
    assert credentials.load_creds()["last_local_dir"] == "/data/cells"
    # Doesn't clobber the rest of the store.
    credentials.save_cred("https://dl.example.com", "key123")
    loaded = credentials.load_creds()
    assert loaded["last_local_dir"] == "/data/cells"
    assert loaded["keys"] == {"https://dl.example.com": "key123"}


def test_save_local_dir_ignores_blank(creds_file: Path) -> None:
    credentials.save_local_dir("   ")
    assert not creds_file.exists()


def test_old_creds_file_without_field(creds_file: Path) -> None:
    creds_file.write_text(
        json.dumps({"last_url": "https://x", "keys": {}, "auto_connect": True}),
        encoding="utf-8",
    )
    assert credentials.load_creds()["last_local_dir"] == ""
