"""Connection panel for the Dash GUI: navbar status + modal.

Credential persistence + the connect / auto-connect / sign-out flow live
in :mod:`datalab_plot.credentials`; this module is the Dash rendering
shell.

Rendering surface:
  * **Disconnected**: navbar shows a Connect button → opens a ``dbc.Modal``
    with URL + API key + Connect. Auto-connect runs silently on first page
    load.
  * **Connected**: navbar shows ``[● ServerName ▾]`` — a dropdown with a
    cache-stats header (N cells cached · X MB on disk · Y MB parsed in
    memory) + Forget cached data / Forget saved key / Sign out.

Callback summary:
  * ``_on_first_load`` — fires once per page load, attempts auto-connect.
  * ``_render_status`` — re-renders the navbar status whenever
    ``connection-version`` or ``plot-version`` changes (so cache stats
    refresh after a plot fetches new data).
  * ``_on_connect_click`` — manual Connect button (modal submit).
  * ``_open_modal`` — Connect-in-navbar opens the modal.
  * ``_on_signout_click`` / ``_on_forget_data_click`` /
    ``_on_forget_key_click`` — dropdown menu items.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from datalab_plot.credentials import (
    connect,
    creds_path,
    forget_cred,
    load_creds,
    normalize_url,
    save_cred,
    save_local_dir,
    saved_key_for,
    set_auto_connect,
)
from datalab_plot.gui_dash.state import clear_state, get_state
from datalab_plot.local_source import connect_local
from datalab_plot.plot_constants import _ENV_KEY, _ENV_URL, DEFAULT_URL

logger = logging.getLogger(__name__)


def _resolve_autoconnect_target() -> tuple[str, str, str]:
    """Return ``(url, key, source)`` to pre-fill / auto-connect with.

    ``source`` is ``"saved"`` (from credentials.json), ``"env"`` (from the
    DATALAB_API_KEY env var) or ``""`` (no key available). Saved keys beat
    env keys for the *same URL* — once a user has connected with a fresh
    key, a stale env key must not be retried on restart.
    """
    saved = load_creds()
    target_url = saved["last_url"] or _ENV_URL or DEFAULT_URL
    auto_key = saved_key_for(target_url)
    source = "saved" if auto_key else ""
    if not auto_key and normalize_url(target_url) == normalize_url(_ENV_URL):
        auto_key = _ENV_KEY
        source = "env" if auto_key else ""
    return target_url, auto_key, source


def _do_connect(url: str, api_key: str) -> tuple[bool, str]:
    """Connect, persist creds on success, store client in session state.

    Returns ``(ok, message)``. ``message`` is the server name on success or
    a one-line error string on failure.
    """
    state = get_state()
    try:
        client = connect(url, api_key)
    except Exception as exc:
        logger.warning("Connect failed", exc_info=True)
        return False, f"{type(exc).__name__}: {exc}".replace("\n", " ")
    try:
        info = client.client.get_info()
        server_name = (
            info.get("data", {}).get("attributes", {}).get("name")
            or client.client.datalab_api_url
        )
    except Exception:
        server_name = client.client.datalab_api_url
    state["client"] = client
    state["server_name"] = server_name
    try:
        save_cred(url, api_key)
    except OSError:
        logger.warning("Could not persist credentials", exc_info=True)
    return True, server_name


def _human_size(n_bytes: float) -> str:
    """Compact byte-size string. 1234 -> '1.2 KB'; 1500000 -> '1.5 MB'."""
    if n_bytes < 1024:
        return f"{int(n_bytes)} B"
    for unit in ("KB", "MB", "GB"):
        n_bytes /= 1024
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
    return f"{n_bytes:.1f} TB"


_CACHE_SKIP_DIRS = frozenset({"saved_plots"})


def _cache_stats() -> str:
    """Return a one-line summary of the on-disk cell cache.

    Format: ``'12 cells cached · 247 MB on disk · 18 MB parsed in memory'``.
    The cell count is the number of item-id subdirectories under
    ``client.cache_root`` that contain at least one file. ``saved_plots/``
    (config snapshots) is excluded from both the count and the disk size —
    those are configs, not cached cell data.
    """
    state = get_state()

    client = state.get("client")
    n_on_disk = 0
    on_disk_bytes = 0
    if client is not None:
        try:
            root = Path(client.cache_root)
            if root.exists():
                for entry in root.iterdir():
                    if not entry.is_dir() or entry.name in _CACHE_SKIP_DIRS:
                        continue
                    has_files = False
                    for f in entry.rglob("*"):
                        if f.is_file():
                            try:
                                on_disk_bytes += f.stat().st_size
                                has_files = True
                            except OSError:
                                pass
                    if has_files:
                        n_on_disk += 1
        except Exception:
            pass

    raw_data: dict = state.get("raw_data") or {}
    in_mem = 0
    for df in raw_data.values():
        try:
            in_mem += int(df.memory_usage(deep=True).sum())
        except Exception:
            pass

    parts = [f"{n_on_disk} cells cached"]
    if on_disk_bytes > 0:
        parts.append(f"{_human_size(on_disk_bytes)} on disk")
    if in_mem > 0:
        parts.append(f"{_human_size(in_mem)} parsed in memory")
    return " · ".join(parts)


def _pick_folder_macos() -> str | None:
    """macOS folder picker via ``osascript`` (always present).

    Preferred over tkinter on darwin: uv-managed python-build-standalone
    interpreters bundle tkinter but ship a broken Tcl runtime path, so
    ``tk.Tk()`` raises TclError even though ``import tkinter`` works.
    """
    try:
        out = subprocess.run(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt '
                '"Choose a folder of cycler files")',
            ],
            capture_output=True,
            text=True,
            timeout=300,  # generous — the dialog waits on the user
        )
    except Exception:
        logger.warning("osascript folder dialog failed", exc_info=True)
        return None
    if out.returncode != 0:
        # AppleScript error -128 = user pressed Cancel — not a failure.
        err = (out.stderr or "").lower()
        if "-128" in err or "canceled" in err or "cancelled" in err:
            return ""
        logger.warning("osascript folder dialog error: %s", err.strip()[:200])
        return None
    return out.stdout.strip()


def _pick_folder_tkinter() -> str | None:
    """Fallback folder picker via a tkinter SUBPROCESS (Windows/Linux).

    Never call tkinter on a Flask worker thread — on macOS creating an
    NSWindow off the main thread crashes the process, and other
    platforms have their own threading constraints.
    """
    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.wm_attributes('-topmost', 1)\n"
        "print(filedialog.askdirectory() or '')\n"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception:
        logger.warning("Folder dialog subprocess failed", exc_info=True)
        return None
    if out.returncode != 0:
        logger.warning(
            "Folder dialog unavailable: %s", (out.stderr or "").strip()[:200]
        )
        return None
    return out.stdout.strip()


def _pick_folder_native() -> str | None:
    """Open a native folder picker and return the chosen path.

    Returns ``""`` if the user cancelled and ``None`` if no dialog could
    be shown (no display, missing toolkit, timeout).
    """
    if sys.platform == "darwin":
        return _pick_folder_macos()
    return _pick_folder_tkinter()


def _local_dir_prefill() -> str:
    """Initial value for the Local-folder path input.

    Last successfully opened folder beats the env var.
    """
    return load_creds()["last_local_dir"] or os.environ.get(
        "DATALAB_PLOT_LOCAL_DIR", ""
    )


def _local_stats(client) -> str:  # type: ignore[no-untyped-def]
    """Header line for the local-folder dropdown.

    Format: ``'14 cycling files · /data/cells · 18 MB parsed in memory'``.
    """
    try:
        n_files = len(client.list_files())
    except Exception:
        n_files = 0
    parts = [f"{n_files} cycling files", str(client.root)]
    raw_data: dict = get_state().get("raw_data") or {}
    in_mem = 0
    for df in raw_data.values():
        try:
            in_mem += int(df.memory_usage(deep=True).sum())
        except Exception:
            pass
    if in_mem > 0:
        parts.append(f"{_human_size(in_mem)} parsed in memory")
    return " · ".join(parts)


def _format_autoconnect_msg(source: str, server_msg: str) -> str:
    where = {
        "saved": (
            f"the key saved by this app ({creds_path()}) — use Connect "
            "with a fresh key to overwrite it"
        ),
        "env": (
            "the DATALAB_API_KEY environment variable inherited from the "
            "shell that launched the GUI — unset it (or fix your .env) and "
            "restart, or just Connect with a fresh key below"
        ),
    }.get(source, "the stored key")
    return f"Auto-connect failed using {where}. Server said: {server_msg}"


def _connect_modal() -> dbc.Modal:
    """The Connect modal: a segmented source selector, one pane each,
    one submit each.

    Datalab server and Local folder are mutually exclusive connection
    methods. A connected ButtonGroup (the app's house segmented-control
    pattern — dbc.Tabs rendered full-width-stacked inside the modal)
    switches between two always-mounted panes, each carrying its own
    solid-primary submit — exactly one primary action visible at a
    time. The footer carries only Cancel. Both panes stay mounted
    (style-toggled), so every input/button remains a valid callback
    Input regardless of the visible pane.
    """
    target_url, auto_key, _src = _resolve_autoconnect_target()
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Connect to data")),
            dbc.ModalBody(
                [
                    html.Div(id="conn-modal-autoconn-msg", className="mb-2"),
                    # Source selector — segmented control, datalab default.
                    html.Div(
                        dbc.ButtonGroup(
                            [
                                dbc.Button(
                                    "Datalab server",
                                    id="conn-mode-datalab",
                                    color="secondary",
                                    outline=True,
                                    size="sm",
                                    active=True,
                                ),
                                dbc.Button(
                                    "Local folder",
                                    id="conn-mode-local",
                                    color="secondary",
                                    outline=True,
                                    size="sm",
                                ),
                            ],
                            size="sm",
                        ),
                        className="mb-3",
                    ),
                    # Pane: datalab server (visible by default).
                    html.Div(
                        [
                            dbc.Label(
                                "Datalab URL",
                                html_for="conn-url",
                                className="ui-field-label",
                            ),
                            dbc.Input(
                                id="conn-url",
                                type="text",
                                value=target_url,
                                placeholder="https://datalab.example.com/",
                                size="sm",
                                className="mb-2",
                            ),
                            dbc.Label(
                                "API key",
                                html_for="conn-key",
                                className="ui-field-label",
                            ),
                            dbc.Input(
                                id="conn-key",
                                type="password",
                                value=auto_key,
                                placeholder="paste your datalab API key",
                                size="sm",
                                className="mb-2",
                            ),
                            html.Div(
                                dbc.Button(
                                    "Connect",
                                    id="conn-connect-btn",
                                    color="primary",
                                    size="sm",
                                ),
                                className="d-flex justify-content-end",
                            ),
                        ],
                        id="conn-pane-datalab",
                    ),
                    # Pane: local folder (hidden until selected). Plain
                    # Div with no `.d-*` utility class, so an inline
                    # style toggle is safe (cf. the staged-apply-fields
                    # `.d-flex !important` lesson).
                    html.Div(
                        [
                            dbc.Label(
                                "Folder path",
                                html_for="conn-local-path",
                                className="ui-field-label",
                            ),
                            dbc.InputGroup(
                                [
                                    dbc.Input(
                                        id="conn-local-path",
                                        type="text",
                                        value=_local_dir_prefill(),
                                        placeholder="/path/to/cycling/data",
                                        size="sm",
                                    ),
                                    dbc.Button(
                                        "Browse…",
                                        id="conn-local-browse-btn",
                                        color="secondary",
                                        outline=True,
                                        size="sm",
                                    ),
                                ],
                                size="sm",
                                className="mb-2",
                            ),
                            html.Div(
                                "All cycler exports in the folder "
                                "(recursively) are listed — .mpr, "
                                ".nda/.ndax, .res, .xls(x), .csv, .txt.",
                                className="ui-caption mb-2",
                            ),
                            html.Div(
                                dbc.Button(
                                    "Open folder",
                                    id="conn-local-btn",
                                    color="primary",
                                    size="sm",
                                ),
                                className="d-flex justify-content-end",
                            ),
                        ],
                        id="conn-pane-local",
                        style={"display": "none"},
                    ),
                    html.Div(id="conn-error", className="ui-feedback ui-feedback-danger"),
                ]
            ),
            dbc.ModalFooter(
                dbc.Button(
                    "Cancel",
                    id="conn-modal-cancel",
                    color="secondary",
                    outline=True,
                    size="sm",
                ),
            ),
        ],
        id="conn-modal",
        is_open=False,
        size="md",
    )


def layout() -> html.Div:
    """Connection layout — placed into the navbar by ``app.py``.

    Returns a container that holds the status item (button or dropdown)
    plus the Connect modal. The status item is re-rendered by the
    ``_render_status`` callback whenever ``connection-version`` changes.
    """
    return html.Div(
        [
            html.Div(id="connection-status"),  # navbar item, callback-driven
            _connect_modal(),
        ]
    )


def register_callbacks(app: dash.Dash) -> None:
    # --- One-shot auto-connect on first page load -------------------------
    @app.callback(
        Output("connection-version", "data", allow_duplicate=True),
        Input("url", "pathname"),
        State("connection-version", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def _on_first_load(_pathname: str, version: int):  # type: ignore[no-untyped-def]
        state = get_state()
        if state.get("client") is not None:
            return no_update
        if state.get("signed_out"):
            return no_update
        saved = load_creds()
        if not saved["auto_connect"]:
            return no_update
        target_url, auto_key, source = _resolve_autoconnect_target()
        if not target_url or not auto_key:
            return no_update
        ok, msg = _do_connect(target_url, auto_key)
        if not ok:
            state["auto_connect_failed"] = msg
            state["auto_connect_source"] = source
            return no_update
        return (version or 0) + 1

    # --- Render the navbar status whenever connection-version OR
    # plot-version changes (so cache stats refresh after every plot fetch).
    @app.callback(
        Output("connection-status", "children"),
        Output("conn-modal-autoconn-msg", "children"),
        Input("connection-version", "data"),
        Input("plot-version", "data"),
    )
    def _render_status(_cv: int, _pv: int):  # type: ignore[no-untyped-def]
        state = get_state()
        client = state.get("client")
        autoconn_msg: html.Div | str = ""
        if state.get("auto_connect_failed"):
            autoconn_msg = dbc.Alert(
                _format_autoconnect_msg(
                    state.get("auto_connect_source", ""),
                    state["auto_connect_failed"],
                ),
                color="warning",
            )
        if client is None:
            return (
                dbc.Button(
                    "Connect",
                    id="conn-open-modal",
                    color="primary",
                    size="sm",
                ),
                autoconn_msg,
            )
        server_name = state.get("server_name", "connected")
        is_local = getattr(client, "is_local", False)
        # All dropdown-item ids must stay mounted in BOTH modes — they're
        # callback Inputs, and a Dash callback can't fire if any Input is
        # missing from the layout. Local mode hides the datalab-only
        # actions instead of dropping them.
        has_saved_key = (not is_local) and bool(
            saved_key_for(client.client.datalab_api_url)
        )
        _hidden = {"display": "none"}
        items = [
            dbc.DropdownMenuItem(
                _local_stats(client) if is_local else _cache_stats(),
                header=True,
            ),
            dbc.DropdownMenuItem(divider=True),
            dbc.DropdownMenuItem(
                "Forget cached data",
                id="conn-forget-data-btn",
                n_clicks=0,
                # Hidden in local mode: there is no per-item download
                # cache, and the rmtree behind this action must never
                # run anywhere near a user's data folder.
                style=_hidden if is_local else None,
            ),
            dbc.DropdownMenuItem(
                "Forget saved key",
                id="conn-forget-key-btn",
                n_clicks=0,
                style=None if has_saved_key else _hidden,
            ),
            dbc.DropdownMenuItem(divider=True),
            dbc.DropdownMenuItem(
                "Close folder" if is_local else "Sign out",
                id="conn-signout-btn",
                n_clicks=0,
            ),
        ]
        return (
            dbc.DropdownMenu(
                items,
                label=[
                    html.Span("●", className="connection-status-dot"),
                    server_name,
                ],
                color="light",
                size="sm",
                align_end=True,
            ),
            autoconn_msg,
        )

    # --- Open the Connect modal from the navbar button --------------------
    @app.callback(
        Output("conn-modal", "is_open"),
        Input("conn-open-modal", "n_clicks"),
        Input("conn-modal-cancel", "n_clicks"),
        Input("connection-version", "data"),
        State("conn-modal", "is_open"),
        prevent_initial_call=True,
    )
    def _modal_open_close(open_clicks, cancel_clicks, _version, is_open):  # type: ignore[no-untyped-def]
        from dash import ctx
        triggered = ctx.triggered_id
        if triggered == "conn-open-modal":
            return True
        if triggered == "conn-modal-cancel":
            return False
        if triggered == "connection-version":
            # A successful connect bumps the version — close the modal.
            state = get_state()
            if state.get("client") is not None:
                return False
        return is_open

    # --- Manual Connect button (submit in modal) --------------------------
    @app.callback(
        Output("connection-version", "data", allow_duplicate=True),
        Output("conn-error", "children"),
        Input("conn-connect-btn", "n_clicks"),
        State("conn-url", "value"),
        State("conn-key", "value"),
        State("connection-version", "data"),
        prevent_initial_call=True,
    )
    def _on_connect_click(n_clicks, url, api_key, version):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        if not api_key:
            return no_update, "API key is required."
        state = get_state()
        state.pop("auto_connect_failed", None)
        state.pop("auto_connect_source", None)
        state.pop("signed_out", None)
        ok, msg = _do_connect(url or "", api_key or "")
        if not ok:
            return no_update, f"Connection failed: {msg}"
        return (version or 0) + 1, ""

    # --- Source-mode segmented control (Datalab server | Local folder) ----
    @app.callback(
        Output("conn-mode-datalab", "active"),
        Output("conn-mode-local", "active"),
        Output("conn-pane-datalab", "style"),
        Output("conn-pane-local", "style"),
        Input("conn-mode-datalab", "n_clicks"),
        Input("conn-mode-local", "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_mode_switch(_d, _l):  # type: ignore[no-untyped-def]
        from dash import ctx

        local = ctx.triggered_id == "conn-mode-local"
        return (
            not local,
            local,
            {"display": "none"} if local else {},
            {} if local else {"display": "none"},
        )

    # --- Browse… → native folder dialog (Local-folder tab) -----------------
    @app.callback(
        Output("conn-local-path", "value"),
        Output("conn-error", "children", allow_duplicate=True),
        Input("conn-local-browse-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_browse_click(n_clicks):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        picked = _pick_folder_native()
        if picked is None:
            return no_update, (
                "Folder dialog unavailable on this system — "
                "type the path instead."
            )
        if not picked:  # user cancelled — keep whatever was typed
            return no_update, no_update
        return picked, ""

    # --- Open a local folder (Local-folder tab's submit) -------------------
    @app.callback(
        Output("connection-version", "data", allow_duplicate=True),
        Output("conn-error", "children", allow_duplicate=True),
        Input("conn-local-btn", "n_clicks"),
        State("conn-local-path", "value"),
        State("connection-version", "data"),
        prevent_initial_call=True,
    )
    def _on_open_local_click(n_clicks, path, version):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update, no_update
        state = get_state()
        try:
            source = connect_local(path or "")
        except (ValueError, NotADirectoryError) as exc:
            return no_update, str(exc)
        state.pop("auto_connect_failed", None)
        state.pop("auto_connect_source", None)
        state.pop("signed_out", None)
        state["client"] = source
        state["server_name"] = source.root.name
        # Remember the (resolved) folder so the next launch pre-fills it.
        save_local_dir(str(source.root))
        return (version or 0) + 1, ""

    # --- Sign out / Close folder ------------------------------------------
    @app.callback(
        Output("connection-version", "data", allow_duplicate=True),
        Input("conn-signout-btn", "n_clicks"),
        State("connection-version", "data"),
        prevent_initial_call=True,
    )
    def _on_signout_click(n_clicks, version):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update
        state = get_state()
        client = state.get("client")
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.warning("client.close() failed", exc_info=True)
        clear_state()
        fresh = get_state()
        fresh["signed_out"] = True
        set_auto_connect(False)
        return (version or 0) + 1

    # --- Forget cached data (in-memory parsed dict + on-disk item caches) -
    @app.callback(
        Output("connection-version", "data", allow_duplicate=True),
        Input("conn-forget-data-btn", "n_clicks"),
        State("connection-version", "data"),
        prevent_initial_call=True,
    )
    def _on_forget_data_click(n_clicks, version):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update
        state = get_state()
        # 1. Clear in-memory parsed DataFrames + cached masses + last figure.
        state["raw_data"] = {}
        state["cathode_masses"] = {}
        state.pop("last_fig", None)
        state.pop("last_plot", None)
        state.pop("last_cycle_summaries", None)
        # 2. Delete on-disk item-cache subdirs (but keep saved_plots/).
        import shutil

        client = state.get("client")
        if client is not None:
            try:
                root = Path(client.cache_root)
                if root.exists():
                    for entry in root.iterdir():
                        if not entry.is_dir() or entry.name in _CACHE_SKIP_DIRS:
                            continue
                        try:
                            shutil.rmtree(entry)
                        except OSError:
                            logger.warning(
                                "Could not delete cache dir %s", entry, exc_info=True,
                            )
            except Exception:
                logger.warning("Could not walk cache_root", exc_info=True)
        return (version or 0) + 1

    # --- Forget saved key -------------------------------------------------
    @app.callback(
        Output("connection-version", "data", allow_duplicate=True),
        Input("conn-forget-key-btn", "n_clicks"),
        State("connection-version", "data"),
        prevent_initial_call=True,
    )
    def _on_forget_key_click(n_clicks, version):  # type: ignore[no-untyped-def]
        if not n_clicks:
            return no_update
        state = get_state()
        client = state.get("client")
        # Local sources have no datalab key (button is hidden, but guard
        # anyway — clicking it would crash on the missing .client).
        if client is None or getattr(client, "is_local", False):
            return no_update
        forget_cred(client.client.datalab_api_url)
        return (version or 0) + 1
