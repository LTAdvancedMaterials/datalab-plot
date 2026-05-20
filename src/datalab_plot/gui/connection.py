"""Connection sidebar and credential persistence.

The datalab_api client only reads the key from os.environ, so without a store
the GUI forgets every key on restart and auto-connect keeps retrying the
(possibly stale) .env key. We persist successful connections to a JSON file in
the platform user-config dir — the industry-standard place (cf. `gh`, `aws`,
`docker`), keyed by instance URL so each datalab keeps its own key. The file is
chmod 0600 (owner read/write only).
"""
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

import platformdirs
import streamlit as st

from datalab_plot.client import DatalabPlotClient
from datalab_plot.gui.constants import _ENV_KEY, _ENV_URL, DEFAULT_URL, PICKER_KEY_BASE

logger = logging.getLogger(__name__)


def _creds_path() -> Path:
    return Path(platformdirs.user_config_dir("datalab-plot")) / "credentials.json"


def _normalize_url(url: str | None) -> str:
    """Canonical form for use as a credentials-dict key (trailing-slash- and
    whitespace-insensitive)."""
    return (url or "").strip().rstrip("/")


def _default_creds() -> dict[str, Any]:
    # auto_connect is the persisted "stay signed out" preference: a browser
    # refresh starts a fresh Streamlit session (st.session_state is wiped),
    # so an in-session signed-out flag can't survive one. This disk flag
    # can. Default True — auto-connect unless the user has signed out.
    return {"last_url": "", "keys": {}, "auto_connect": True}


def _load_creds() -> dict[str, Any]:
    """Return ``{"last_url": str, "keys": {url: key}, "auto_connect": bool}``;
    defaults on any read/parse failure (a corrupt file must never break the
    app)."""
    try:
        data = json.loads(_creds_path().read_text(encoding="utf-8"))
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
        path = _creds_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner only
        except OSError:
            pass
    except OSError:
        logger.warning("Could not write credentials file", exc_info=True)


def _saved_key_for(url: str | None) -> str:
    return _load_creds()["keys"].get(_normalize_url(url), "")


def _save_cred(url: str, api_key: str) -> None:
    """Persist ``api_key`` for ``url``, record it as the last-used URL, and
    re-enable auto-connect — a manual connect is an explicit opt back in."""
    data = _load_creds()
    norm = _normalize_url(url)
    if not norm or not api_key:
        return
    data["keys"][norm] = api_key.strip()
    data["last_url"] = norm
    data["auto_connect"] = True
    _write_creds(data)


def _forget_cred(url: str) -> None:
    """Drop the stored key for ``url``. Best-effort."""
    data = _load_creds()
    norm = _normalize_url(url)
    if data["keys"].pop(norm, None) is None:
        return
    if data.get("last_url") == norm:
        data["last_url"] = ""
    _write_creds(data)


def _set_auto_connect(enabled: bool) -> None:
    """Persist the auto-connect preference (keys untouched). Sign-out sets
    this False so a subsequent browser refresh doesn't silently reconnect."""
    data = _load_creds()
    data["auto_connect"] = bool(enabled)
    _write_creds(data)


def _connect(url: str, api_key: str) -> DatalabPlotClient:
    """Open an authenticated client for ``(url, api_key)``.

    The datalab_api client only reads the key from ``os.environ`` — which
    is process-global and would otherwise leak across browser sessions and
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
        # Restore os.environ to the immutable startup snapshot so a
        # different session's auto-connect can't pick up this key.
        if _ENV_KEY:
            os.environ["DATALAB_API_KEY"] = _ENV_KEY
        else:
            os.environ.pop("DATALAB_API_KEY", None)
    # Validate the key — raises RuntimeError on a 401 / bad key.
    c.client.authenticate()
    return c


def _store_connection(c: DatalabPlotClient) -> None:
    info = c.client.get_info()
    st.session_state["client"] = c
    st.session_state["server_name"] = (
        info.get("data", {}).get("attributes", {}).get("name")
        or c.client.datalab_api_url
    )


def _connect_and_store(url: str, api_key: str) -> None:
    """Open + validate a connection, register it, and persist the key.

    The key is saved only after ``_connect`` succeeds (it raises on a bad
    key), so a stale key is never written. Persistence itself is
    best-effort — a read-only config dir won't block a working session.
    """
    _store_connection(_connect(url, api_key))
    try:
        _save_cred(url, api_key)
    except OSError:
        logger.warning("Could not persist credentials after connect", exc_info=True)


def _sidebar_connection() -> DatalabPlotClient | None:
    client: DatalabPlotClient | None = st.session_state.get("client")

    if client is None:
        # Resolve the URL + key to pre-fill and auto-connect with. A key
        # saved from a previous successful connection wins over the .env
        # key — that's the whole point: once the user connects with a
        # fresh key, a stale .env key must stop being retried on restart.
        # The .env key is used only as a fallback, and only for the URL it
        # belongs to.
        saved = _load_creds()
        target_url = saved["last_url"] or _ENV_URL or DEFAULT_URL
        auto_key = _saved_key_for(target_url)
        # Track where the auto-connect key came from so a failure message
        # can name the real source — "saved" (this app's credentials.json)
        # vs. "env" (the DATALAB_API_KEY environment variable the GUI
        # process inherited). Those need different fixes.
        key_source = "saved" if auto_key else ""
        if not auto_key and _normalize_url(target_url) == _normalize_url(_ENV_URL):
            auto_key = _ENV_KEY
            key_source = "env"

        if (
            target_url and auto_key
            and saved["auto_connect"]
            and not st.session_state.get("auto_connect_failed")
            and not st.session_state.get("signed_out")
        ):
            try:
                _connect_and_store(target_url, auto_key)
                st.rerun()
            except Exception as exc:
                logger.warning("Auto-connect failed", exc_info=True)
                st.session_state["auto_connect_failed"] = str(exc)
                st.session_state["auto_connect_source"] = key_source

        st.sidebar.subheader("Connect")
        if st.session_state.get("auto_connect_failed"):
            src = st.session_state.get("auto_connect_source", "")
            where = {
                "saved": (
                    "the key saved by this app "
                    f"(`{_creds_path()}`) — use *Connect* with a fresh key "
                    "to overwrite it"
                ),
                "env": (
                    "the `DATALAB_API_KEY` environment variable inherited "
                    "from the shell that launched the GUI — unset it (or "
                    "fix your `.env`) and restart, or just Connect with a "
                    "fresh key below"
                ),
            }.get(src, "the stored key")
            st.sidebar.warning(
                f"Auto-connect failed using {where}.\n\n"
                f"Server said: {st.session_state['auto_connect_failed']}"
            )
        url = st.sidebar.text_input("Datalab URL", value=target_url, key="ui_url")
        api_key = st.sidebar.text_input(
            "API key",
            value=auto_key,
            type="password",
            help=(
                "On a successful connect this key is saved per-URL at "
                f"`{_creds_path()}` (owner-only file permissions), so you "
                "won't need to re-enter it next time."
            ),
            key="ui_api_key",
        )
        if st.sidebar.button("Connect", type="primary", use_container_width=True):
            if not api_key:
                st.sidebar.error("API key is required.")
            else:
                st.session_state.pop("auto_connect_failed", None)
                st.session_state.pop("auto_connect_source", None)
                st.session_state.pop("signed_out", None)
                try:
                    _connect_and_store(url, api_key)
                    st.rerun()
                except Exception as exc:
                    logger.warning("Manual connect failed", exc_info=True)
                    st.sidebar.error(f"Connection failed: {exc}")
        return None

    st.sidebar.success(f"✓ {st.session_state.get('server_name', 'connected')}")
    with st.sidebar.expander("Connection", expanded=False):
        st.write(f"**URL** `{client.client.datalab_api_url}`")
        st.write(f"**Cache** `{client.cache_root}`")
        st.caption(
            f"{len(st.session_state.get('raw_data', {}))} cell(s) parsed and in memory."
        )
        if st.button("Forget parsed data", use_container_width=True):
            st.session_state["raw_data"] = {}
            st.rerun()
        if _saved_key_for(client.client.datalab_api_url):
            if st.button(
                "Forget saved key", use_container_width=True,
                help="Delete the stored API key for this URL. You'll need "
                     "to re-enter it next time.",
            ):
                _forget_cred(client.client.datalab_api_url)
                st.rerun()
        if st.button("Sign out", type="secondary", use_container_width=True):
            try:
                client.close()
            finally:
                for k in list(st.session_state.keys()):
                    if str(k).startswith(f"{PICKER_KEY_BASE}_v") or k in (
                        "client", "server_name", "results", "picker_initial",
                        "picker_version", "picker_last_edited",
                        "raw_data", "last_plot", "last_fig",
                        "last_plot_signature", "broken_items",
                        "ui_preset", "ui_mode",
                        "ui_x_axis", "ui_y_axis", "ui_y2_axis",
                        "ui_cycle", "ui_title", "ui_live",
                        "ui_color_by_status", "ui_width_scale",
                        "ui_plot_width", "ui_plot_height",
                        "ui_border", "ui_grid_x", "ui_grid_y",
                        "ui_legend_mode", "ui_font_size", "ui_colorbar",
                        "ui_xmin", "ui_xmax", "ui_ymin", "ui_ymax",
                        "ui_y2min", "ui_y2max",
                    ):
                        st.session_state.pop(k, None)
                # Suppress auto-connect — otherwise the saved / env-var key
                # would sign the user straight back in. The session flag
                # covers this session; the disk flag survives a browser
                # refresh (which wipes session_state). Both are cleared
                # when the user explicitly clicks Connect again.
                st.session_state["signed_out"] = True
                _set_auto_connect(False)
                st.rerun()
    return client
