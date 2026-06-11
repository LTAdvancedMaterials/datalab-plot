"""Search row: query the connected datalab instance and feed the picker.

Auto-populate on first connect runs as a one-shot callback fired by
``connection-version``; manual searches are triggered by the search
button or Enter in the input.
"""
from __future__ import annotations

import logging
from typing import Any

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, html, no_update

from datalab_plot.gui_dash.state import get_state
from datalab_plot.picker_helpers import _build_initial_df
from datalab_plot.plot_helpers import _empty_picker_df
from datalab_plot.search import find_cells

logger = logging.getLogger(__name__)

# Number of most-recent items to pre-load on first connect.
_AUTOPOPULATE_COUNT = 30


def _current_picker_df_from_state() -> pd.DataFrame:
    """Return the most recent picker frame from state, or an empty frame."""
    last = get_state().get("picker_last_edited")
    if last is not None and not last.empty:
        return last.copy()
    initial = get_state().get("picker_initial")
    if initial is not None:
        return initial.copy()
    return _empty_picker_df()


def _prior_selected_dict(prior: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if prior.empty:
        return {}
    return {
        r["item_id"]: r.to_dict()
        for _, r in prior.iterrows()
        if bool(r["Select"])
    }


def layout() -> html.Div:
    return html.Div(
        [
            dbc.InputGroup(
                [
                    dbc.Input(
                        id="search-input",
                        type="text",
                        placeholder=(
                            "e.g. NMC811 — leave blank to list everything "
                            "(press Enter or click Search)"
                        ),
                        debounce=False,
                        size="sm",
                    ),
                    dbc.Button(
                        # The label is wrapped in a dbc.Spinner whose
                        # inner Span is an Output of `_on_search`. While
                        # that callback runs, Dash flags the Span's
                        # loading_state and the Spinner replaces the
                        # "Search" text with a spinner — reverting on
                        # completion. minWidth keeps the button from
                        # collapsing to spinner-width mid-search.
                        dbc.Spinner(
                            html.Span("Search", id="search-btn-label"),
                            size="sm",
                            color="primary",
                        ),
                        id="search-btn",
                        color="primary",
                        outline=True,
                        size="sm",
                        style={"minWidth": "5rem"},
                    ),
                ],
                size="sm",
                className="mb-2",
            ),
            html.Div(id="search-error", className="ui-feedback ui-feedback-danger mb-2"),
            html.Div(id="search-summary", className="ui-meta"),
        ]
    )


def register_callbacks(app: dash.Dash) -> None:
    # --- Auto-populate on first connect -----------------------------------
    @app.callback(
        Output("search-version", "data", allow_duplicate=True),
        Output("search-summary", "children", allow_duplicate=True),
        Output("search-error", "children", allow_duplicate=True),
        Input("connection-version", "data"),
        State("search-version", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def _autopopulate(_conn_version, search_version):  # type: ignore[no-untyped-def]
        state = get_state()
        client = state.get("client")
        if client is None:
            return no_update, no_update, no_update
        if state.get("results") is not None:
            # Already populated this session.
            return no_update, no_update, no_update
        is_local = getattr(client, "is_local", False)
        try:
            if is_local:
                # Folder listing is cheap (and capped) — show everything.
                df = client.list_files()
            else:
                df = find_cells(
                    item_type=("samples", "cells"),
                    limit=_AUTOPOPULATE_COUNT,
                    client=client,
                )
        except Exception as exc:
            logger.warning("Auto-populate failed", exc_info=True)
            state["results"] = pd.DataFrame()
            return no_update, no_update, f"Auto-populate failed: {exc}"
        state["results"] = df
        summary = (
            f"{len(df)} cycling files in {client.root} — search above to filter."
            if is_local
            else f"Showing the {len(df)} most recent items — search above to load others."
        )
        state["picker_initial"] = _build_initial_df(df, None).reset_index(drop=True)
        state.pop("picker_last_edited", None)
        return (search_version or 0) + 1, summary, ""

    # --- Manual search ---------------------------------------------------
    # The `search-btn-label` output is a no-op text-wise (always returns
    # no_update); it exists only so the Span enters loading_state while
    # this callback runs, which the wrapping dbc.Spinner turns into an
    # in-button spinner. See layout().
    @app.callback(
        Output("search-version", "data", allow_duplicate=True),
        Output("search-summary", "children", allow_duplicate=True),
        Output("search-error", "children", allow_duplicate=True),
        Output("search-btn-label", "children"),
        Input("search-btn", "n_clicks"),
        Input("search-input", "n_submit"),
        State("search-input", "value"),
        State("search-version", "data"),
        prevent_initial_call=True,
    )
    def _on_search(_n_clicks, _n_submit, query, search_version):  # type: ignore[no-untyped-def]
        state = get_state()
        client = state.get("client")
        if client is None:
            return no_update, no_update, no_update, no_update
        is_local = getattr(client, "is_local", False)
        try:
            if is_local:
                df = client.list_files(query)
            else:
                df = find_cells(
                    query=(query or None),
                    item_type=("samples", "cells"),
                    limit=300,
                    client=client,
                )
        except Exception as exc:
            logger.warning("Search failed", exc_info=True)
            return no_update, no_update, f"Search failed: {exc}", no_update
        noun = "file(s)" if is_local else "result(s)"
        summary = (
            f"{len(df)} {noun} for “{query}”"
            if query
            else f"Showing all {len(df)} {'files' if is_local else 'items'}"
        )
        prior = _current_picker_df_from_state()
        state["results"] = df
        state["picker_initial"] = _build_initial_df(
            df, _prior_selected_dict(prior)
        ).reset_index(drop=True)
        state.pop("picker_last_edited", None)
        return (search_version or 0) + 1, summary, "", no_update
