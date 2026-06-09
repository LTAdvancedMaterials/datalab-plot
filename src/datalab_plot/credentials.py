"""Credential persistence and ``_connect`` shared by both GUI back-ends.

The datalab_api client only reads the key from ``os.environ``, so without a
store the GUI forgets every key on restart and auto-connect keeps retrying
the (possibly stale) ``.env`` key. We persist successful connections to a
JSON file in the platform user-config dir — the industry-standard place (cf.
``gh``, ``aws``, ``docker``), keyed by instance URL so each datalab keeps
its own key. The file is chmod 0600 (owner read/write only).

This module is pure (no Streamlit, no Dash). Both ``gui/connection.py`` and
``gui_dash/connection.py`` import from here.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

import platformdirs

from datalab_plot.client import DatalabPlotClient
from datalab_plot.plot_constants import _ENV_KEY

logger = logging.getLogger(__name__)


def creds_path() -> Path:
    return Path(platformdirs.user_config_dir("datalab-plot")) / "credentials.json"


def normalize_url(url: str | None) -> str:
    """Canonical form for use as a credentials-dict key (trailing-slash- and
    whitespace-insensitive)."""
    return (url or "").strip().rstrip("/")


def _default_creds() -> dict[str, Any]:
    # auto_connect is the persisted "stay signed out" preference: a browser
    # refresh starts a fresh session (session_state is wiped), so an
    # in-session signed-out flag can't survive one. This disk flag can.
    # Default True — auto-connect unless the user has signed out.
    return {"last_url": "", "keys": {}, "auto_connect": True}


def load_creds() -> dict[str, Any]:
    """Return ``{"last_url": str, "keys": {url: key}, "auto_connect": bool}``;
    defaults on any read/parse failure (a corrupt file must never break the
    app)."""
    try:
        data = json.loads(creds_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return _default_creds()
    if not isinstance(data, dict):
        return _default_creds()
    keys = data.get("keys")
    return {
        "last_url": data.get("last_url") or "",
        "keys": keys if isinstance(keys, dict) else {},
        "auto_connect": data.get("auto_connect", True) is not False,
    }


def _write_creds(data: dict[str, Any]) -> None:
    """Write the credentials dict to disk, 0600. Best-effort — a read-only
    config dir must not break the app."""
    try:
        path = creds_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner only
        except OSError:
            pass
    except OSError:
        logger.warning("Could not write credentials file", exc_info=True)


def saved_key_for(url: str | None) -> str:
    return load_creds()["keys"].get(normalize_url(url), "")


def save_cred(url: str, api_key: str) -> None:
    """Persist ``api_key`` for ``url``, record it as the last-used URL, and
    re-enable auto-connect — a manual connect is an explicit opt back in."""
    data = load_creds()
    norm = normalize_url(url)
    if not norm or not api_key:
        return
    data["keys"][norm] = api_key.strip()
    data["last_url"] = norm
    data["auto_connect"] = True
    _write_creds(data)


def forget_cred(url: str) -> None:
    """Drop the stored key for ``url``. Best-effort."""
    data = load_creds()
    norm = normalize_url(url)
    if data["keys"].pop(norm, None) is None:
        return
    if data.get("last_url") == norm:
        data["last_url"] = ""
    _write_creds(data)


def set_auto_connect(enabled: bool) -> None:
    """Persist the auto-connect preference (keys untouched). Sign-out sets
    this False so a subsequent browser refresh doesn't silently reconnect."""
    data = load_creds()
    data["auto_connect"] = bool(enabled)
    _write_creds(data)


def connect(url: str, api_key: str) -> DatalabPlotClient:
    """Open an authenticated client for ``(url, api_key)``.

    The datalab_api client only reads the key from ``os.environ`` — which is
    process-global and would otherwise leak across browser sessions and
    survive a sign-out. We set it just long enough for construction (the
    client caches the key internally during ``__init__``), then restore
    ``os.environ`` to the pristine ``.env`` snapshot.

    The connection is validated with an authenticated request: the client
    constructor's ``get_info()`` call is unauthenticated, so a wrong key
    would otherwise produce a false "connected" state.

    ``url`` / ``api_key`` are stripped — a trailing newline or space (very
    common in a key pulled from ``.env`` or pasted from another app) would
    otherwise be sent in the auth header and rejected by the server.
    """
    url = (url or "").strip()
    api_key = (api_key or "").strip()
    os.environ["DATALAB_API_KEY"] = api_key
    try:
        c = DatalabPlotClient(url)
        c.__enter__()
    finally:
        # Restore os.environ to the immutable startup snapshot so a different
        # session's auto-connect can't pick up this key.
        if _ENV_KEY:
            os.environ["DATALAB_API_KEY"] = _ENV_KEY
        else:
            os.environ.pop("DATALAB_API_KEY", None)
    # Validate the key — raises RuntimeError on a 401 / bad key.
    c.client.authenticate()
    return c
