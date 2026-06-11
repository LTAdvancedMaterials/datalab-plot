"""Local-folder data source: plot cycler files without a datalab server.

``LocalFolderSource`` duck-types the slice of :class:`DatalabPlotClient`
that the plot pipeline (``plotly_builders._ensure_data_for``) consumes —
``get_item`` / ``fetch_files_verbose`` / ``purge`` — backed by a folder
of cycler exports instead of a datalab instance. One file = one item;
``item_id`` is the file's path relative to the chosen root (posix form).

SAFETY INVARIANT: this source must NEVER modify or delete the user's
data files. ``purge`` is a hard no-op (a force-refresh re-parses from
disk anyway), and ``cache_root`` points at the normal app cache dir,
never at the data folder — so "Forget cached data"-style rmtree logic
can't touch user data even if invoked.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import pandas as pd

from .cache import cache_dir
from .client import FilePredicate
from .parsers.echem import CYCLING_EXTENSIONS

logger = logging.getLogger(__name__)

# Hard cap on the recursive scan so a pathological root (e.g. "/")
# can't hang the listing.
_SCAN_LIMIT = 2000


@runtime_checkable
class DataSource(Protocol):
    """The minimal interface ``_ensure_data_for`` needs from a source.

    Satisfied by both :class:`DatalabPlotClient` (datalab-backed) and
    :class:`LocalFolderSource` (folder-backed).
    """

    def get_item(self, item_id: str) -> dict: ...

    def fetch_files_verbose(
        self,
        item_id: str,
        predicate: FilePredicate | None = None,
        *,
        item: dict | None = None,
    ) -> list[tuple[Path, Literal["hit", "miss"]]]: ...

    def purge(self, item_id: str) -> None: ...


class LocalFolderSource:
    """A folder of cycler files presented through the DataSource interface.

    ``item_id`` == file path relative to ``root`` (posix). All formats in
    ``CYCLING_EXTENSIONS`` are listed; parsing is delegated to
    ``parsers.echem.load_echem`` by the plot pipeline exactly as for
    datalab-fetched files.
    """

    #: Cheap discriminator for GUI branches (``getattr(c, "is_local", False)``).
    is_local = True

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(f"Not a folder: {self.root}")
        # NOT the data folder — used only for parsed-in-memory stats.
        self.cache_root = cache_dir()

    # -- listing (feeds the GUI picker) ---------------------------------

    def list_files(
        self, query: str | None = None, limit: int = _SCAN_LIMIT
    ) -> pd.DataFrame:
        """Recursively scan for cycling files → find_cells-shaped frame.

        ``query`` is a case-insensitive substring filter on the relative
        path. Columns match ``search.find_cells`` so the picker helpers
        consume the frame unchanged; metadata columns (electrodes,
        electrolyte, mass) are empty/None — a bare file carries none.
        """
        q = (query or "").strip().lower()
        rows: list[dict[str, Any]] = []
        capped = False
        for p in sorted(self.root.rglob("*")):
            if len(rows) >= limit:
                capped = True
                break
            if not p.is_file():
                continue
            if not p.name.lower().endswith(CYCLING_EXTENSIONS):
                continue
            rel = p.relative_to(self.root).as_posix()
            if q and q not in rel.lower():
                continue
            rows.append(
                {
                    "item_id": rel,
                    "name": p.name,
                    "refcode": "",
                    "type": "file",
                    "chemform": "",
                    "positive_electrode": "",
                    "negative_electrode": "",
                    "electrolyte": "",
                    "last_modified": pd.Timestamp(
                        p.stat().st_mtime, unit="s"
                    ).isoformat(),
                    "collections": "",
                    "cathode_mass_mg": None,
                }
            )
        if capped:
            logger.warning(
                "Folder scan capped at %d files under %s", limit, self.root
            )
        return pd.DataFrame(
            rows,
            columns=[
                "item_id", "name", "refcode", "type", "chemform",
                "positive_electrode", "negative_electrode", "electrolyte",
                "last_modified", "collections", "cathode_mass_mg",
            ],
        )

    # -- DataSource interface (consumed by _ensure_data_for) ------------

    def get_item(self, item_id: str) -> dict:
        """No per-file metadata — cathode mass etc. stay None."""
        return {}

    def fetch_files_verbose(
        self,
        item_id: str,
        predicate: FilePredicate | None = None,
        *,
        item: dict | None = None,
    ) -> list[tuple[Path, Literal["hit", "miss"]]]:
        """Resolve ``item_id`` to its file. Always a "hit" — the file IS
        the source; nothing is downloaded or copied.

        Returns ``[]`` for ids that escape the root (e.g. ``../x`` from a
        stale saved config) or no longer exist on disk.
        """
        p = (self.root / item_id).resolve()
        if not p.is_relative_to(self.root) or not p.is_file():
            return []
        if predicate is not None and not predicate({"name": p.name}):
            return []
        return [(p, "hit")]

    def purge(self, item_id: str) -> None:
        """No-op — NEVER delete user data files (safety invariant).

        A force-refresh re-parses from disk regardless, which is the
        correct "the file changed" semantics for a local source.
        """
        return None

    def close(self) -> None:
        """Parity with DatalabPlotClient.close() — nothing to release."""
        return None


def connect_local(path: str) -> LocalFolderSource:
    """Validate ``path`` and return a LocalFolderSource for it.

    Raises ``ValueError`` with a user-facing message on a missing /
    non-directory path. An empty folder (no cycling files) is allowed —
    the GUI shows a zero-results listing rather than refusing to open.
    """
    raw = (path or "").strip()
    if not raw:
        raise ValueError("Enter a folder path.")
    p = Path(raw).expanduser()
    if not p.exists():
        raise ValueError(f"Folder not found: {p}")
    if not p.is_dir():
        raise ValueError(f"Not a folder: {p}")
    return LocalFolderSource(p)
