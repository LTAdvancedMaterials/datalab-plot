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
                        "Search",
                        id="search-btn",
                        color="primary",
                        outline=True,
                        size="sm",
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
        try:
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
        summary = f"Showing the {len(df)} most recent items — search above to load others."
        state["picker_initial"] = _build_initial_df(df, None).reset_index(drop=True)
        state.pop("picker_last_edited", None)
        return (search_version or 0) + 1, summary, ""

    # --- Manual search ---------------------------------------------------
    @app.callback(
        Output("search-version", "data", allow_duplicate=True),
        Output("search-summary", "children", allow_duplicate=True),
        Output("search-error", "children", allow_duplicate=True),
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
            return no_update, no_update, no_update
        try:
            df = find_cells(
                query=(query or None),
                item_type=("samples", "cells"),
                limit=300,
                client=client,
            )
        except Exception as exc:
            logger.warning("Search failed", exc_info=True)
            return no_update, no_update, f"Search failed: {exc}"
        summary = (
            f"{len(df)} result(s) for “{query}”"
            if query
            else f"Showing all {len(df)} items"
        )
        prior = _current_picker_df_from_state()
        state["results"] = df
        state["picker_initial"] = _build_initial_df(
            df, _prior_selected_dict(prior)
        ).reset_index(drop=True)
        state.pop("picker_last_edited", None)
        return (search_version or 0) + 1, summary, ""
