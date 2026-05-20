"""Search row: query the connected datalab instance and feed the picker."""
from __future__ import annotations

import logging

import streamlit as st

from datalab_plot.client import DatalabPlotClient
from datalab_plot.gui.picker_panel import _build_initial_df, _current_picker_df, _set_initial
from datalab_plot.search import find_cells

logger = logging.getLogger(__name__)


def _do_search() -> None:
    """Run a search using ``ui_query`` and the connected client.

    Used both by the Search button (inline) and as the text input's
    ``on_change`` callback so pressing Enter in the search box triggers
    the search without an extra click.
    """
    client: DatalabPlotClient | None = st.session_state.get("client")
    if client is None:
        return
    query = st.session_state.get("ui_query", "") or None
    try:
        df = find_cells(
            query=query,
            item_type=("samples", "cells"),
            limit=300,
            client=client,
        )
        st.session_state["results"] = df
        prior = _current_picker_df()
        prior_by_id = (
            {
                r["item_id"]: r.to_dict()
                for _, r in prior.iterrows()
                if bool(r["Select"])
            }
            if not prior.empty
            else {}
        )
        _set_initial(_build_initial_df(df, prior_by_id))
        st.session_state["_search_error"] = None
    except Exception as exc:
        logger.warning("Search failed", exc_info=True)
        st.session_state["_search_error"] = str(exc)


def _search_section(client: DatalabPlotClient) -> None:
    """Render the search row inside ``st.form`` so Enter always submits —
    a text_input's ``on_change`` callback only fires when the value changes,
    which means pressing Enter on an empty field with no prior value would
    silently do nothing. The form's submit event fires on Enter regardless."""
    with st.form("search_form", clear_on_submit=False, border=False):
        cols = st.columns([6, 1])
        cols[0].text_input(
            "Search items",
            value=st.session_state.get("ui_query", ""),
            placeholder="e.g. NMC811 — leave blank to list everything (press Enter to search)",
            label_visibility="collapsed",
            key="ui_query",
        )
        submitted = cols[1].form_submit_button(
            "Search", use_container_width=True,
        )
    if submitted:
        _do_search()
    err = st.session_state.pop("_search_error", None)
    if err:
        st.error(f"Search failed: {err}")
