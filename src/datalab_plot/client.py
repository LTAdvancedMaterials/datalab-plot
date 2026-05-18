"""A thin wrapper around :class:`datalab_api.DatalabClient` that caches downloaded files."""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from datalab_api import DatalabClient

from .cache import cache_dir


FileMeta = dict
FilePredicate = Callable[[FileMeta], bool]


class DatalabPlotClient:
    """Wrap a :class:`DatalabClient` with a per-item file cache.

    Resolves the datalab URL from the ``DATALAB_URL`` env var if not given,
    matching the convention in the project's ``datalab prompt.md``.
    """

    def __init__(self, url: str | None = None, *, cache_root: Path | None = None) -> None:
        url = url or os.environ.get("DATALAB_URL")
        if not url:
            raise ValueError(
                "DATALAB_URL is not set. Pass a url or set the DATALAB_URL env var."
            )
        self.client = DatalabClient(url)
        self.cache_root = cache_root or cache_dir()

    def __enter__(self) -> DatalabPlotClient:
        self.client.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.client.__exit__(exc_type, exc, tb)

    def close(self) -> None:
        self.client.__exit__(None, None, None)

    def fetch_files(
        self,
        item_id: str,
        predicate: FilePredicate | None = None,
        *,
        item: dict | None = None,
    ) -> list[Path]:
        """Download files attached to ``item_id`` matching ``predicate``.

        Returns the local paths in the order they appear in the item's file list.
        Files already on disk with a matching size are reused without re-downloading.
        Pass ``item`` if you've already fetched the item dict to save a round-trip.
        """
        if item is None:
            item = self.client.get_item(item_id=item_id)
        item_cache = self.cache_root / item_id
        item_cache.mkdir(parents=True, exist_ok=True)

        paths: list[Path] = []
        for f in item.get("files") or []:
            if predicate is not None and not predicate(f):
                continue
            dest = item_cache / f["name"]
            if dest.exists() and dest.stat().st_size == f["size"]:
                paths.append(dest)
                continue
            url = f"{self.client.datalab_api_url}/files/{f['immutable_id']}/{f['name']}"
            with self.client.session.stream("GET", url, follow_redirects=True) as resp:
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        fh.write(chunk)
            paths.append(dest)
        return paths

    def fetch_files_verbose(
        self,
        item_id: str,
        predicate: FilePredicate | None = None,
        *,
        item: dict | None = None,
    ) -> list[tuple[Path, Literal["hit", "miss"]]]:
        """Like :meth:`fetch_files`, but also reports whether each file was a
        cache hit or had to be re-downloaded.

        Returns a list of ``(local_path, "hit" | "miss")`` tuples in the same
        order as the item's file list.
        """
        if item is None:
            item = self.client.get_item(item_id=item_id)
        item_cache = self.cache_root / item_id
        item_cache.mkdir(parents=True, exist_ok=True)

        results: list[tuple[Path, Literal["hit", "miss"]]] = []
        for f in item.get("files") or []:
            if predicate is not None and not predicate(f):
                continue
            dest = item_cache / f["name"]
            if dest.exists() and dest.stat().st_size == f["size"]:
                results.append((dest, "hit"))
                continue
            url = f"{self.client.datalab_api_url}/files/{f['immutable_id']}/{f['name']}"
            with self.client.session.stream("GET", url, follow_redirects=True) as resp:
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        fh.write(chunk)
            results.append((dest, "miss"))
        return results

    def purge(self, item_id: str) -> None:
        """Delete the local cache subdirectory for ``item_id``.

        The next :meth:`fetch_files` call will re-download every file.
        """
        item_cache = self.cache_root / item_id
        if item_cache.exists():
            shutil.rmtree(item_cache)


def _resolve_client(client: DatalabPlotClient | None) -> tuple[DatalabPlotClient, bool]:
    """Return ``(client, owns)`` where ``owns`` is True if we should close it."""
    if client is not None:
        return client, False
    c = DatalabPlotClient()
    c.__enter__()
    return c, True
