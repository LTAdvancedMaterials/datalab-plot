"""Confirm-modals for destructive actions: Delete saved plot + Reset.

Each modal has a "Don't show this again" checkbox whose dismissal
persists per-browser via ``dcc.Store(id="suppress-confirms",
storage_type="local")`` — Dash's built-in localStorage backing, no JS.

Callback graph (delete; reset is analogous):

  🗑 button click
      → fork: if suppress.delete → bump confirm-delete-trigger; else open modal
  modal OK click
      → bump confirm-delete-trigger
      → close modal
      → (if "don't show again" ticked) write suppress["delete"] = True
  modal Cancel click
      → close modal

The actual delete logic lives in ``export.py:_delete``, listening on
``Input("confirm-delete-trigger", "data")``. The reset action lives in
``app.py`` listening on ``Input("confirm-reset-trigger", "data")``.
"""
from __future__ import annotations

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update


def delete_modal() -> dbc.Modal:
    """Confirm modal for "Delete saved plot" (Export panel)."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Delete saved plot?")),
            dbc.ModalBody(
                [
                    html.P(
                        "The saved JSON file will be removed from your "
                        "cache. This can't be undone.",
                        className="mb-2",
                    ),
                    dbc.Checkbox(
                        id="confirm-delete-dontshow",
                        label="Don't show this again",
                        value=False,
                    ),
                ]
            ),
            dbc.ModalFooter(
                [
                    dbc.Button(
                        "Cancel",
                        id="confirm-delete-cancel",
                        color="secondary",
                        outline=True,
                        size="sm",
                    ),
                    dbc.Button(
                        "Delete",
                        id="confirm-delete-ok",
                        color="primary",
                        size="sm",
                    ),
                ]
            ),
        ],
        id="confirm-delete-modal",
        is_open=False,
        size="md",
    )


def reset_modal() -> dbc.Modal:
    """Confirm modal for the navbar Reset action."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Reset to a blank slate?")),
            dbc.ModalBody(
                [
                    html.P(
                        "Clears staged cells, search results, the plot, "
                        "and resets all plot options to defaults. You "
                        "stay connected, and any already-parsed cell "
                        "data stays cached.",
                        className="mb-2",
                    ),
                    dbc.Checkbox(
                        id="confirm-reset-dontshow",
                        label="Don't show this again",
                        value=False,
                    ),
                ]
            ),
            dbc.ModalFooter(
                [
                    dbc.Button(
                        "Cancel",
                        id="confirm-reset-cancel",
                        color="secondary",
                        outline=True,
                        size="sm",
                    ),
                    dbc.Button(
                        "Reset",
                        id="confirm-reset-ok",
                        color="primary",
                        size="sm",
                    ),
                ]
            ),
        ],
        id="confirm-reset-modal",
        is_open=False,
        size="md",
    )


def register_callbacks(app: dash.Dash) -> None:
    # --- Delete fork: 🗑 click → open modal (or skip if suppressed) ------
    @app.callback(
        Output("confirm-delete-modal", "is_open", allow_duplicate=True),
        Output("confirm-delete-trigger", "data", allow_duplicate=True),
        Input("export-delete-btn", "n_clicks"),
        State("suppress-confirms", "data"),
        State("confirm-delete-trigger", "data"),
        prevent_initial_call=True,
    )
    def _maybe_open_delete(n, suppress, trigger):  # type: ignore[no-untyped-def]
        if not n:
            return no_update, no_update
        if (suppress or {}).get("delete"):
            return False, (trigger or 0) + 1
        return True, no_update

    # --- Delete confirm: OK click → fire action + close + maybe suppress
    @app.callback(
        Output("confirm-delete-modal", "is_open", allow_duplicate=True),
        Output("confirm-delete-trigger", "data", allow_duplicate=True),
        Output("suppress-confirms", "data", allow_duplicate=True),
        Input("confirm-delete-ok", "n_clicks"),
        State("confirm-delete-dontshow", "value"),
        State("confirm-delete-trigger", "data"),
        State("suppress-confirms", "data"),
        prevent_initial_call=True,
    )
    def _confirm_delete(n, dontshow, trigger, suppress):  # type: ignore[no-untyped-def]
        if not n:
            return no_update, no_update, no_update
        new_suppress = dict(suppress or {})
        if dontshow:
            new_suppress["delete"] = True
        return False, (trigger or 0) + 1, new_suppress

    # --- Delete cancel: just close ----------------------------------------
    @app.callback(
        Output("confirm-delete-modal", "is_open", allow_duplicate=True),
        Input("confirm-delete-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def _cancel_delete(n):  # type: ignore[no-untyped-def]
        return False if n else no_update

    # --- Reset fork: navbar Reset click → open modal (or skip) -----------
    @app.callback(
        Output("confirm-reset-modal", "is_open", allow_duplicate=True),
        Output("confirm-reset-trigger", "data", allow_duplicate=True),
        Input("reset-btn", "n_clicks"),
        State("suppress-confirms", "data"),
        State("confirm-reset-trigger", "data"),
        prevent_initial_call=True,
    )
    def _maybe_open_reset(n, suppress, trigger):  # type: ignore[no-untyped-def]
        if not n:
            return no_update, no_update
        if (suppress or {}).get("reset"):
            return False, (trigger or 0) + 1
        return True, no_update

    # --- Reset confirm: OK click → fire action + close + maybe suppress
    @app.callback(
        Output("confirm-reset-modal", "is_open", allow_duplicate=True),
        Output("confirm-reset-trigger", "data", allow_duplicate=True),
        Output("suppress-confirms", "data", allow_duplicate=True),
        Input("confirm-reset-ok", "n_clicks"),
        State("confirm-reset-dontshow", "value"),
        State("confirm-reset-trigger", "data"),
        State("suppress-confirms", "data"),
        prevent_initial_call=True,
    )
    def _confirm_reset(n, dontshow, trigger, suppress):  # type: ignore[no-untyped-def]
        if not n:
            return no_update, no_update, no_update
        new_suppress = dict(suppress or {})
        if dontshow:
            new_suppress["reset"] = True
        return False, (trigger or 0) + 1, new_suppress

    # --- Reset cancel: just close -----------------------------------------
    @app.callback(
        Output("confirm-reset-modal", "is_open", allow_duplicate=True),
        Input("confirm-reset-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def _cancel_reset(n):  # type: ignore[no-untyped-def]
        return False if n else no_update


__all__ = ["delete_modal", "reset_modal", "register_callbacks"]
