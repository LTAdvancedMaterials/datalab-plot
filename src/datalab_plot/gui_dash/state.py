"""Per-browser-session in-memory state for the Dash GUI.

Mirrors the role of Streamlit's ``st.session_state``: a per-tab Python dict
that survives across callback invocations and dies when the server stops.
Lookup is by ``session_id`` (a UUID minted on first request and held in a
Flask signed-cookie ``session["sid"]``).

Why not ``dcc.Store``? ``DatalabPlotClient`` and parsed DataFrames don't
serialise cleanly to JSON, and we'd need server-side referencing anyway.
This is simpler for a single-user local tool.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from flask import session

_LOCK = threading.Lock()
_STORES: dict[str, dict[str, Any]] = {}


def session_id() -> str:
    """Return the current request's session id, minting one if absent."""
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
        # Long-lived cookie so the browser tab keeps the same state across
        # page reloads. (The in-memory store still dies with the server.)
        session.permanent = True
    return sid


def get_state() -> dict[str, Any]:
    """Return the state dict for the current Flask session.

    Carries the same load-bearing keys the Streamlit GUI uses:
    ``client``, ``server_name``, ``results``, ``raw_data``,
    ``cathode_masses``, ``broken_items``, ``last_fig``, ``last_plot``,
    ``last_cycle_summaries``, ``picker_initial``.

    Dash-only additions:
      * ``staged_items`` — durable list of dicts ``{item_id, name, label,
        group, color}`` driving the plot. Survives new searches; reset by
        sign-out (``clear_state()``).
    """
    sid = session_id()
    with _LOCK:
        store = _STORES.get(sid)
        if store is None:
            store = {}
            _STORES[sid] = store
    return store


def clear_state() -> None:
    """Drop the state dict for the current session (e.g. on sign-out)."""
    sid = session.get("sid")
    if not sid:
        return
    with _LOCK:
        _STORES.pop(sid, None)


def reset_ui_state() -> None:
    """Wipe interactive UI state for the current session.

    Keeps ``client``, ``raw_data``, and ``cathode_masses`` so re-staging
    the same cells doesn't re-fetch. Heavier wipes live in
    ``clear_state`` (sign-out) and ``connection.py``'s "Forget cached
    data" (raw_data + on-disk cache).
    """
    state = get_state()
    state["staged_items"] = []
    state["results"] = None
    state["picker_initial"] = None
    state.pop("last_fig", None)
    state.pop("last_plot", None)
    state.pop("last_cycle_summaries", None)
    state.pop("broken_items", None)
