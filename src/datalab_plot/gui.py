"""Streamlit GUI for datalab-plot. Launch via ``datalab-plot gui``."""
from __future__ import annotations

import io
import os
from typing import Any

import matplotlib
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

matplotlib.use("Agg")
import streamlit as st  # noqa: E402

# Streamlit runs this file as a top-level script, so use absolute imports.
from datalab_plot.client import DatalabPlotClient  # noqa: E402
from datalab_plot.parsers.echem import (  # noqa: E402
    compute_dqdv,
    cycle_summary,
    filter_by_cycle,
    is_cycling_file,
    load_echem,
    split_half_cycles,
)
from datalab_plot.plots.echem import (  # noqa: E402
    _assign_colors,
    _cumulative_time_hours,
    _normalise_items,
    plot_cycles,
)
from datalab_plot.search import find_cells  # noqa: E402


DEFAULT_URL = "https://datalab.lightningtree.ai/"
PICKER_COLUMNS = ("Select", "item_id", "name", "chemform", "label", "group", "color")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _rgba_to_css(c: Any) -> str:
    from matplotlib.colors import to_rgba

    r, g, b, a = to_rgba(c)
    return f"rgba({int(r * 255)}, {int(g * 255)}, {int(b * 255)}, {a:.3f})"


def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    return buf.getvalue()


def _empty_picker_df() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="object") for c in PICKER_COLUMNS})
    df["Select"] = df["Select"].astype(bool)
    return df


# ---------------------------------------------------------------------------
# Picker state: version-bumped key pattern.
#
# Streamlit forbids ALL external writes to st.session_state[<data_editor key>]
# (callbacks, before-first-render, ... it doesn't matter — the check fires the
# next time the widget renders). So the only way to programmatically change a
# data_editor's state is to make it a *different* widget. We do that by
# bumping a version counter that is part of the widget's `key`.
#
#   * picker_initial   -- the DataFrame fed into the editor. Replaced only by
#                         `_set_initial`; never mutated in place.
#   * picker_version   -- integer; bumped by `_set_initial`. Part of the key.
#   * picker_last_edited -- the editor's return value from the previous
#                         render. Bulk handlers use this to preserve the
#                         user's per-row edits (labels/groups/colours) when
#                         constructing the new picker_initial.
# ---------------------------------------------------------------------------

PICKER_KEY_BASE = "picker_editor"


def _picker_widget_key() -> str:
    return f"{PICKER_KEY_BASE}_v{st.session_state.get('picker_version', 0)}"


def _build_initial_df(results: pd.DataFrame, prior_selected: dict[str, dict[str, Any]] | None) -> pd.DataFrame:
    """Build a fresh initial picker DataFrame from search results.

    ``prior_selected`` (item_id → row dict) carries forward selections /
    label / group / color across new searches.
    """
    prior_selected = prior_selected or {}
    rows: list[dict[str, Any]] = []
    seen_in_results: set[str] = set()
    for _, r in results.iterrows():
        iid = r["item_id"]
        if not iid:
            continue
        seen_in_results.add(iid)
        prev = prior_selected.get(iid, {})
        rows.append(
            {
                "Select": bool(prev.get("Select", False)),
                "item_id": iid,
                "name": r.get("name", "") or "",
                "chemform": r.get("chemform", "") or "",
                "label": prev.get("label") or (r.get("name") or iid),
                "group": prev.get("group", "") or "",
                "color": prev.get("color", "") or "",
            }
        )
    for iid, prev in prior_selected.items():
        if iid in seen_in_results or not prev.get("Select"):
            continue
        rows.append(
            {
                "Select": True,
                "item_id": iid,
                "name": prev.get("name", "") or "",
                "chemform": prev.get("chemform", "") or "",
                "label": prev.get("label") or iid,
                "group": prev.get("group", "") or "",
                "color": prev.get("color", "") or "",
            }
        )

    if not rows:
        return _empty_picker_df()
    df = pd.DataFrame(rows, columns=list(PICKER_COLUMNS))
    df["Select"] = df["Select"].astype(bool)
    return df


def _set_initial(new_df: pd.DataFrame) -> None:
    """Replace the immutable initial DataFrame and bump the widget version
    so the data_editor re-mounts with the new values."""
    st.session_state["picker_initial"] = new_df.reset_index(drop=True)
    st.session_state["picker_version"] = st.session_state.get("picker_version", 0) + 1
    # The editor's return-value snapshot is also stale now.
    st.session_state.pop("picker_last_edited", None)


def _current_picker_df() -> pd.DataFrame:
    """Most recent edited frame from the previous data_editor render, or the
    initial frame if the editor hasn't rendered yet (or was just bumped)."""
    last: pd.DataFrame | None = st.session_state.get("picker_last_edited")
    if last is not None and not last.empty:
        return last.copy()
    return st.session_state.get("picker_initial", _empty_picker_df()).copy()


# --- Bulk-action callbacks. Each builds a new initial DataFrame from the
# user's current edits (so labels / groups / colours are preserved) with the
# Select column overwritten, then bumps the widget version. -----------------

def _cb_select_all() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    current["Select"] = True
    _set_initial(current)


def _cb_select_none() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    current["Select"] = False
    _set_initial(current)


def _cb_invert() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    current["Select"] = ~current["Select"].fillna(False).astype(bool)
    _set_initial(current)


def _cb_check_range() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    start = int(st.session_state.get("range_from", 1))
    end = int(st.session_state.get("range_to", len(current)))
    lo, hi = sorted((start, end))
    current = current.reset_index(drop=True)
    current.loc[current.index[lo - 1 : hi], "Select"] = True
    _set_initial(current)


def _cb_uncheck_range() -> None:
    current = _current_picker_df()
    if current.empty:
        return
    start = int(st.session_state.get("range_from", 1))
    end = int(st.session_state.get("range_to", len(current)))
    lo, hi = sorted((start, end))
    current = current.reset_index(drop=True)
    current.loc[current.index[lo - 1 : hi], "Select"] = False
    _set_initial(current)


def _selected_payload(picker_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Build the dict[label, {item_id, group?, color?}] shape plot_cycles takes."""
    payload: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    if picker_df.empty:
        return payload
    for _, r in picker_df.iterrows():
        if not bool(r["Select"]):
            continue
        label = ((r.get("label") or "") or "").strip() or r["item_id"]
        original = label
        i = 2
        while label in seen:
            label = f"{original} ({i})"
            i += 1
        seen.add(label)
        spec: dict[str, Any] = {"item_id": r["item_id"]}
        grp = ((r.get("group") or "") or "").strip()
        col = ((r.get("color") or "") or "").strip()
        if grp:
            spec["group"] = grp
        if col:
            spec["color"] = col
        payload[label] = spec
    return payload


# ---------------------------------------------------------------------------
# Plotly figure builders. Each takes the same payload+raw_data shape so a
# single dispatch can serve both Plot-click and live-update reruns.
# ---------------------------------------------------------------------------

def _layout(fig: go.Figure, height: int, title: str | None = None) -> go.Figure:
    fig.update_layout(
        title=title or None,
        template="plotly_white",
        margin=dict(l=60, r=20, t=50 if title else 30, b=80),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
        hovermode="closest",
        height=height,
    )
    return fig


def _plotly_summary(items, raw, colors, height) -> go.Figure:
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Discharge capacity", "Coulombic efficiency"),
    )
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        summ = cycle_summary(raw[label])
        color = _rgba_to_css(colors[label])
        fig.add_trace(
            go.Scatter(
                x=summ["cycle"], y=summ["Discharge_mAh"],
                mode="lines", name=label,
                line=dict(color=color, width=1.6),
                legendgroup=label,
                hovertemplate="cycle %{x}<br>%{y:.1f} mAh<extra>%{fullData.name}</extra>",
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=summ["cycle"], y=100 * summ["CE"],
                mode="lines", name=label,
                line=dict(color=color, width=1.6),
                legendgroup=label, showlegend=False,
                hovertemplate="cycle %{x}<br>%{y:.2f}%<extra>%{fullData.name}</extra>",
            ),
            row=1, col=2,
        )
    fig.update_xaxes(title_text="Cycle number", row=1, col=1)
    fig.update_xaxes(title_text="Cycle number", row=1, col=2)
    fig.update_yaxes(title_text="Discharge capacity (mAh)", row=1, col=1)
    fig.update_yaxes(title_text="Coulombic efficiency (%)", row=1, col=2, range=[90, 102])
    return _layout(fig, height)


# Perceptually uniform sequential colormaps; one per cell, cycled if more
# cells than colormaps are selected.
PER_CELL_CMAPS = ("viridis", "plasma", "inferno", "magma", "cividis")


def _plotly_voltage_capacity(items, raw, colors, height) -> go.Figure:
    """V-Q for every cycle of every cell. Each cell gets its own perceptually
    uniform colormap; cycles are coloured along that gradient (early = light,
    late = dark for viridis-family). The `colors` param is unused here.
    """
    import matplotlib.pyplot as _plt  # local import to keep cold-start light

    fig = go.Figure()
    for cell_idx, it in enumerate(items):
        label = it["label"]
        if label not in raw:
            continue
        df = raw[label]
        if "full cycle" not in df.columns:
            continue
        cycle_ids = sorted({int(c) for c in df["full cycle"].dropna().unique() if c > 0})
        if not cycle_ids:
            continue
        cmap_name = PER_CELL_CMAPS[cell_idx % len(PER_CELL_CMAPS)]
        cmap = _plt.colormaps[cmap_name]
        n = len(cycle_ids)

        # One invisible legend-only trace per cell so the legend stays compact
        # (one entry per cell, coloured with the colormap's mid-point) and the
        # many real per-cycle traces below share its legendgroup for toggling.
        legend_color = _rgba_to_css(cmap(0.5))
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None], mode="lines",
                name=f"{label}  ({cmap_name})",
                line=dict(color=legend_color, width=3),
                legendgroup=label, showlegend=True,
            )
        )

        for j, cid in enumerate(cycle_ids):
            cyc = df[df["full cycle"] == cid]
            x, y = split_half_cycles(cyc, "Capacity", "Voltage")
            frac = j / max(1, n - 1)
            color = _rgba_to_css(cmap(frac))
            fig.add_trace(
                go.Scattergl(
                    x=x, y=y, mode="lines",
                    line=dict(color=color, width=1.0),
                    legendgroup=label, showlegend=False,
                    hovertemplate=(
                        f"<b>{label}</b> · cycle {cid}<br>"
                        "%{x:.2f} mAh<br>%{y:.3f} V<extra></extra>"
                    ),
                )
            )

    fig.update_xaxes(title_text="Capacity (mAh)")
    fig.update_yaxes(title_text="Voltage (V)")
    return _layout(fig, height, title="Voltage vs capacity, all cycles")


def _plotly_dqdv(items, raw, colors, cycle, height) -> go.Figure:
    fig = go.Figure()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        cyc = filter_by_cycle(raw[label], cycle)
        if cyc.empty:
            continue
        diff = compute_dqdv(cyc, mode="dQ/dV")
        if diff.empty:
            continue
        x, y = split_half_cycles(diff, "voltage (V)", "dQ/dV (mA/V)")
        fig.add_trace(
            go.Scatter(
                x=x, y=y,
                mode="lines", name=label, connectgaps=False,
                line=dict(color=_rgba_to_css(colors[label]), width=1.4),
                hovertemplate="%{x:.3f} V<br>%{y:.2f} mA/V<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_xaxes(title_text="Voltage (V)")
    fig.update_yaxes(title_text="dQ/dV (mA/V)")
    return _layout(fig, height, title=f"dQ/dV, cycle {cycle}")


def _plotly_voltage_time(items, raw, colors, height) -> go.Figure:
    fig = go.Figure()
    for it in items:
        label = it["label"]
        if label not in raw:
            continue
        df = raw[label]
        t = _cumulative_time_hours(df)
        fig.add_trace(
            go.Scatter(
                x=t, y=df["Voltage"],
                mode="lines", name=label,
                line=dict(color=_rgba_to_css(colors[label]), width=1.0),
                hovertemplate="%{x:.2f} h<br>%{y:.3f} V<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_xaxes(title_text="Time (h)")
    fig.update_yaxes(title_text="Voltage (V)")
    return _layout(fig, height, title="Voltage vs time")


def _build_plotly(
    payload: dict[str, dict[str, Any]],
    raw: dict[str, pd.DataFrame],
    mode: str,
    cycle: int | None,
    title: str | None,
    height: int,
) -> go.Figure:
    items = _normalise_items(payload)
    colors = _assign_colors(items)
    if mode == "summary":
        fig = _plotly_summary(items, raw, colors, height)
    elif mode == "voltage_capacity":
        fig = _plotly_voltage_capacity(items, raw, colors, height)
    elif mode == "dqdv":
        fig = _plotly_dqdv(items, raw, colors, int(cycle or 1), height)
    elif mode == "voltage_time":
        fig = _plotly_voltage_time(items, raw, colors, height)
    else:
        raise ValueError(f"Unknown mode {mode!r}")
    if title:
        fig.update_layout(title=title)
    return fig


# ---------------------------------------------------------------------------
# Data acquisition (cached per item_id across reruns)
# ---------------------------------------------------------------------------

def _ensure_data_for(
    client: DatalabPlotClient, item_ids: list[str], *, force: bool
) -> tuple[int, int, list[str], dict[str, str]]:
    """Make sure parsed echem data is loaded for each ``item_id``.

    Returns ``(cache_hits, cache_misses, skipped, errors)`` where:
      - ``skipped`` lists item_ids that have no cycling files attached.
      - ``errors`` maps item_id → short human-readable error message for
        items that failed to fetch or parse.
    Parsed DataFrames are cached in ``st.session_state['raw_data']`` keyed
    by item_id; items that errored are *not* cached so the next attempt
    can retry from scratch.
    """
    raw: dict[str, pd.DataFrame] = st.session_state.setdefault("raw_data", {})
    skipped: list[str] = []
    errors: dict[str, str] = {}
    hits = misses = 0
    for iid in item_ids:
        if not force and iid in raw:
            continue
        if force:
            client.purge(iid)
        try:
            item_dict = client.client.get_item(item_id=iid)
            results = client.fetch_files_verbose(
                iid, predicate=is_cycling_file, item=item_dict
            )
            if not results:
                skipped.append(iid)
                continue
            for _, status in results:
                if status == "hit":
                    hits += 1
                else:
                    misses += 1
            paths = [p for p, _ in results]
            raw[iid] = load_echem(paths)
        except Exception as exc:
            # Drop any partial result and remember the failure so the caller
            # can deselect the row + show the message.
            raw.pop(iid, None)
            errors[iid] = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return hits, misses, skipped, errors


def _raw_keyed_by_label(payload: dict[str, dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """Return raw data re-keyed from item_id to plot label."""
    raw_by_id: dict[str, pd.DataFrame] = st.session_state.get("raw_data", {})
    out: dict[str, pd.DataFrame] = {}
    for label, spec in payload.items():
        iid = spec["item_id"]
        if iid in raw_by_id:
            out[label] = raw_by_id[iid]
    return out


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------

def _sidebar_connection() -> DatalabPlotClient | None:
    client: DatalabPlotClient | None = st.session_state.get("client")

    if client is None:
        st.sidebar.subheader("Connect")
        url = st.sidebar.text_input("Datalab URL", value=DEFAULT_URL, key="ui_url")
        api_key = st.sidebar.text_input(
            "API key",
            value=os.environ.get("DATALAB_API_KEY", ""),
            type="password",
            help="Held in memory for this session only.",
            key="ui_api_key",
        )
        if st.sidebar.button("Connect", type="primary", use_container_width=True):
            if not api_key:
                st.sidebar.error("API key is required.")
            else:
                os.environ["DATALAB_API_KEY"] = api_key
                try:
                    c = DatalabPlotClient(url)
                    c.__enter__()
                    info = c.client.get_info()
                    st.session_state["client"] = c
                    st.session_state["server_name"] = (
                        info.get("data", {}).get("attributes", {}).get("name", url)
                    )
                    st.rerun()
                except Exception as exc:
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
        if st.button("Sign out", type="secondary", use_container_width=True):
            try:
                client.close()
            finally:
                for k in list(st.session_state.keys()):
                    if k.startswith(f"{PICKER_KEY_BASE}_v") or k in (
                        "client", "server_name", "results", "picker_initial",
                        "picker_version", "picker_last_edited",
                        "raw_data", "last_plot", "broken_items",
                    ):
                        st.session_state.pop(k, None)
                st.rerun()
    return client


def _search_section(client: DatalabPlotClient) -> None:
    """Run a search, store the results DataFrame, and reconcile the picker."""
    cols = st.columns([6, 1])
    query = cols[0].text_input(
        "Search items",
        value=st.session_state.get("ui_query", ""),
        placeholder="e.g. NMC811 — leave blank to list everything",
        label_visibility="collapsed",
        key="ui_query",
    )
    if cols[1].button("Search", use_container_width=True):
        with st.spinner("Searching…"):
            try:
                df = find_cells(
                    query=query or None,
                    item_type=("samples", "cells"),
                    limit=300,
                    client=client,
                )
                st.session_state["results"] = df
                # Preserve prior selections by item_id across searches.
                prior = _current_picker_df()
                prior_by_id = {
                    r["item_id"]: r.to_dict()
                    for _, r in prior.iterrows()
                    if bool(r["Select"])
                } if not prior.empty else {}
                _set_initial(_build_initial_df(df, prior_by_id))
            except Exception as exc:
                st.error(f"Search failed: {exc}")


def _picker_table() -> pd.DataFrame:
    initial: pd.DataFrame | None = st.session_state.get("picker_initial")
    if initial is None or initial.empty:
        st.caption("Search to populate the picker. Tick the rows you want to plot.")
        return _empty_picker_df()

    # Show any per-item errors collected on the previous render.
    broken: dict[str, str] = st.session_state.get("broken_items", {})
    if broken:
        err_cols = st.columns([8, 1])
        err_cols[0].error(
            "Couldn't load these items (auto-deselected):\n"
            + "\n".join(f"• **{iid}** — {msg}" for iid, msg in broken.items())
        )
        if err_cols[1].button("Dismiss", use_container_width=True):
            st.session_state["broken_items"] = {}
            st.rerun()

    # Current selection view = initial + editor diff. Used only for counts /
    # bulk-action targeting; do NOT pass it back into the data_editor's data.
    current = _current_picker_df()
    selected_now = int(current["Select"].sum())
    total = len(initial)

    head = st.columns([4, 1, 1, 1])
    head[0].markdown(f"**{selected_now}** selected of {total}")
    head[1].button(
        "All", on_click=_cb_select_all, help="Select every row",
        use_container_width=True, disabled=total == 0,
    )
    head[2].button(
        "None", on_click=_cb_select_none, help="Clear selection",
        use_container_width=True, disabled=selected_now == 0,
    )
    head[3].button(
        "Invert", on_click=_cb_invert, help="Flip every checkbox",
        use_container_width=True, disabled=total == 0,
    )

    with st.expander("Select a range of rows", expanded=False):
        st.caption(f"Rows are 1-indexed (1–{total}).")
        rcol = st.columns([1, 1, 1, 1])
        rcol[0].number_input("From", min_value=1, max_value=total, value=1, key="range_from")
        rcol[1].number_input("To", min_value=1, max_value=total, value=total, key="range_to")
        rcol[2].button("Check range", on_click=_cb_check_range, use_container_width=True)
        rcol[3].button("Uncheck range", on_click=_cb_uncheck_range, use_container_width=True)

    # Version-bumped-key pattern: `data=` is immutable for the lifetime of a
    # given version; bulk actions / new searches build a new initial frame
    # AND bump picker_version, which makes Streamlit instantiate a fresh
    # data_editor whose initial state is the new `initial` DataFrame.
    # We never write to st.session_state[<this widget's key>] (Streamlit
    # forbids it). We read the user's per-row edits from the return value
    # and stash them for the next bulk handler.
    edited = st.data_editor(
        initial,
        hide_index=True,
        use_container_width=True,
        height=min(500, max(220, 38 * (total + 1))),
        column_config={
            "Select": st.column_config.CheckboxColumn("✓", width="small"),
            "item_id": st.column_config.TextColumn("item_id", disabled=True, width="small"),
            "name": st.column_config.TextColumn("name", disabled=True),
            "chemform": st.column_config.TextColumn("chemform", disabled=True, width="small"),
            "label": st.column_config.TextColumn("label", help="Used in the plot legend"),
            "group": st.column_config.TextColumn(
                "group", help="Same group → shared colormap"
            ),
            "color": st.column_config.TextColumn(
                "color", help="Optional colour ('#ff8800', 'C0'). Empty = auto.", width="small"
            ),
        },
        key=_picker_widget_key(),
    )
    edited = edited.copy()
    edited["Select"] = edited["Select"].fillna(False).astype(bool)
    st.session_state["picker_last_edited"] = edited
    return edited


def _plot_bar() -> tuple[str, int | None, str, bool, bool, bool]:
    cols = st.columns([2, 1, 3, 1, 1, 1])
    mode = cols[0].selectbox(
        "Mode",
        ["voltage_time", "summary", "voltage_capacity", "dqdv"],
        key="ui_mode",
    )
    cycle = (
        int(cols[1].number_input("Cycle", min_value=1, step=1, value=1, key="ui_cycle"))
        if mode == "dqdv"
        else None
    )
    if mode != "dqdv":
        cols[1].empty()
    title = cols[2].text_input("Title (optional)", value="", key="ui_title")
    plot_click = cols[3].button(
        "Plot", type="primary", use_container_width=True,
        help="Render the plot from currently-selected cells.",
    )
    refresh_click = cols[4].button(
        "Refresh", use_container_width=True,
        help="Purge local cache for selected items and re-fetch from the server.",
    )
    live = cols[5].toggle(
        "Live",
        value=st.session_state.get("ui_live", False),
        help=(
            "When on, the plot re-renders on every selection change. "
            "Off by default — clicking checkboxes is snappy and you Plot when ready."
        ),
        key="ui_live",
    )
    return mode, cycle, title, plot_click, refresh_click, live


def _plot_size_controls() -> tuple[float, int]:
    cols = st.columns([2, 2, 4])
    width_pct = cols[0].slider(
        "Plot width", min_value=40, max_value=100, value=st.session_state.get("ui_plot_width", 90),
        step=5, format="%d%%", key="ui_plot_width",
    )
    height_px = cols[1].slider(
        "Plot height (px)", min_value=320, max_value=900,
        value=st.session_state.get("ui_plot_height", 520), step=20, key="ui_plot_height",
    )
    return width_pct / 100.0, height_px


def _render_plot(
    client: DatalabPlotClient,
    payload: dict[str, dict[str, Any]],
    mode: str,
    cycle: int | None,
    title: str,
    width_frac: float,
    height_px: int,
    *,
    force_refresh: bool,
) -> None:
    if not payload:
        st.info("Tick rows in the picker to plot.")
        return

    item_ids = [spec["item_id"] for spec in payload.values()]
    need_fetch = force_refresh or any(
        iid not in st.session_state.get("raw_data", {}) for iid in item_ids
    )
    with st.spinner("Fetching files & parsing…" if need_fetch else None):
        hits, misses, skipped, errors = _ensure_data_for(
            client, item_ids, force=force_refresh
        )

    # Per-item failures: build a new initial DataFrame with those rows
    # deselected and bump the widget version so the editor reflects it. We
    # cannot mutate the current editor's state (Streamlit blocks writes to
    # its key); a version bump replaces the widget entirely.
    if errors:
        broken: dict[str, str] = st.session_state.setdefault("broken_items", {})
        broken.update(errors)
        current = _current_picker_df()
        if not current.empty:
            current = current.reset_index(drop=True)
            current.loc[current["item_id"].isin(errors.keys()), "Select"] = False
            _set_initial(current)
        st.rerun()

    if skipped:
        skip_labels = [
            label for label, spec in payload.items() if spec["item_id"] in skipped
        ]
        st.warning(
            "No cycling files for: " + ", ".join(skip_labels) + " — omitted."
        )
        payload = {k: v for k, v in payload.items() if v["item_id"] not in skipped}
        if not payload:
            return

    raw_by_label = _raw_keyed_by_label(payload)
    try:
        fig = _build_plotly(payload, raw_by_label, mode, cycle, title or None, height_px)
    except Exception as exc:
        st.error(f"Plot failed: {exc}")
        return

    # Persist for re-display on subsequent reruns (so checkbox clicks don't
    # have to rebuild + reship the figure).
    st.session_state["last_fig"] = fig
    st.session_state["last_plot"] = {
        "payload": payload, "mode": mode, "cycle": cycle, "title": title,
        "width_frac": width_frac, "height_px": height_px,
        "hits": hits, "misses": misses,
    }

    # Don't render the plot or PNG-export expander here -- main() owns the
    # plot area so the figure persists across reruns at a stable widget key.


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _render_cached_figure() -> None:
    """Re-display the last rendered figure across reruns at a stable widget key.

    This keeps the plot visible while the user toggles checkboxes without
    rebuilding or re-shipping the plotly figure on every click.
    """
    fig = st.session_state.get("last_fig")
    if fig is None:
        return
    cfg = st.session_state.get("last_plot", {})
    width_frac = cfg.get("width_frac", 0.9)
    left_pad = (1.0 - width_frac) / 2
    if left_pad > 0:
        cols = st.columns([left_pad, width_frac, left_pad])
        holder = cols[1]
    else:
        holder = st.container()
    with holder:
        # Stable key — same widget across reruns, so Streamlit/plotly diff
        # rather than re-mount when only ancillary widgets change.
        st.plotly_chart(
            fig, use_container_width=True, key="main_plot",
            config={"displaylogo": False},
        )
    hits, misses = cfg.get("hits", 0), cfg.get("misses", 0)
    if hits + misses:
        st.caption(
            f"Files: {hits}/{hits + misses} cache hit · "
            f"{misses}/{hits + misses} re-downloaded."
        )


def _png_export_section(client: DatalabPlotClient) -> None:
    cfg = st.session_state.get("last_plot")
    if not cfg:
        return
    with st.expander("Export static PNG"):
        if st.button("Generate PNG", key="png_btn"):
            try:
                with st.spinner("Rendering PNG via matplotlib…"):
                    mpl_fig = plot_cycles(
                        cfg["payload"], mode=cfg["mode"], cycle=cfg.get("cycle"),
                        client=client, title=cfg.get("title") or None,
                    )
                st.download_button(
                    "Download PNG",
                    data=_fig_to_png_bytes(mpl_fig),
                    file_name=f"datalab_plot_{cfg['mode']}.png",
                    mime="image/png",
                    key="png_dl",
                )
            except Exception as exc:
                st.error(f"PNG export failed: {exc}")


def main() -> None:
    st.set_page_config(page_title="datalab-plot", layout="wide")
    st.title("datalab-plot")

    client = _sidebar_connection()
    if client is None:
        st.info("Connect to a datalab instance from the sidebar to begin.")
        return

    _search_section(client)
    picker_df = _picker_table()
    mode, cycle, title, plot_click, refresh_click, live = _plot_bar()
    width_frac, height_px = _plot_size_controls()

    payload = _selected_payload(picker_df)

    # Detect whether the live-mode plot inputs changed since the last render —
    # if not, skip the rebuild even with Live on. This keeps checkbox toggles
    # snappy in Live mode when only e.g. the title or width slider moves.
    plot_signature = (
        tuple(sorted((k, v.get("item_id"), v.get("group"), v.get("color"))
                     for k, v in payload.items())),
        mode, cycle, title, width_frac, height_px,
    )
    selection_changed = (
        plot_signature != st.session_state.get("last_plot_signature")
    )

    should_render = (
        plot_click
        or refresh_click
        or (live and selection_changed and payload)
    )

    if should_render:
        _render_plot(
            client, payload, mode, cycle, title, width_frac, height_px,
            force_refresh=refresh_click,
        )
        st.session_state["last_plot_signature"] = plot_signature

    # Always re-display the cached figure (kept stable by key="main_plot"),
    # so checkbox toggles in non-Live mode don't blank the plot.
    if "last_fig" in st.session_state:
        _render_cached_figure()
        _png_export_section(client)
    elif payload:
        st.caption("Click **Plot** to render the selected cells.")
    else:
        st.caption("Tick rows in the picker, then click **Plot**.")


if __name__ == "__main__":
    main()
