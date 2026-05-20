"""Tests for datalab_plot.search — constituent formatting and item discovery."""
from __future__ import annotations

from datalab_plot.search import (
    _as_item_list,
    _format_constituents,
    _row_from_item,
    find_cells,
)

EXPECTED_COLUMNS = [
    "item_id", "name", "refcode", "type", "chemform",
    "positive_electrode", "negative_electrode", "electrolyte",
    "last_modified", "collections",
]


def test_format_constituents_from_item_dicts():
    value = [{"item": {"chemform": "LiCoO2"}}, {"item": {"name": "graphite"}}]
    assert _format_constituents(value) == "LiCoO2 + graphite"


def test_format_constituents_plain_strings_and_fallbacks():
    assert _format_constituents(["NMC", "Li metal"]) == "NMC + Li metal"
    assert _format_constituents([{"chemform": "EC:DMC"}]) == "EC:DMC"
    # chemform empty/None -> fall back to the item name.
    assert _format_constituents([{"item": {"chemform": "", "name": "Li"}}]) == "Li"


def test_format_constituents_empty_or_invalid():
    assert _format_constituents([]) == ""
    assert _format_constituents(None) == ""
    assert _format_constituents("not a list") == ""


def test_row_from_item_uses_datalab_field_names():
    # datalab calls the formula `characteristic_chemical_formula`, and the
    # list endpoint dates the item with `date` (not `last_modified`).
    row = _row_from_item(
        {
            "item_id": "CEL-1",
            "name": "test cell",
            "type": "cells",
            "characteristic_chemical_formula": "LiFePO4",
            "date": "2026-03-03",
            "collections": [{"collection_id": "proj-A"}],
        }
    )
    assert set(row) == set(EXPECTED_COLUMNS)
    assert row["chemform"] == "LiFePO4"
    assert row["last_modified"] == "2026-03-03"
    assert row["collections"] == "proj-A"


def test_row_from_item_prefers_last_modified_over_date():
    row = _row_from_item({"item_id": "X", "last_modified": "newer", "date": "older"})
    assert row["last_modified"] == "newer"


def test_as_item_list():
    assert _as_item_list([{"a": 1}]) == [{"a": 1}]
    assert _as_item_list({"samples": [{"a": 1}], "status": "success"}) == [{"a": 1}]
    assert _as_item_list({"items": [{"b": 2}]}) == [{"b": 2}]
    assert _as_item_list({"status": "error"}) == []
    assert _as_item_list(None) == []


class _FakeInner:
    """Stand-in for datalab_api.DatalabClient.

    Mirrors the real API shapes: `get_items` returns the raw endpoint dict,
    and the per-cell construction fields exist only on `get_item`.
    """

    def __init__(self):
        self.search_calls: list = []
        self.get_item_calls: list = []

    def get_items(self, item_type):
        return {
            "samples": [
                {
                    "item_id": "CEL-1",
                    "name": "cell one",
                    "type": "cells",
                    "characteristic_chemical_formula": "LiCoO2",
                    "date": "2026-01-01",
                }
            ],
            "status": "success",
        }

    def search_items(self, query, item_types):
        self.search_calls.append((query, item_types))
        return [{"item_id": "CEL-9", "name": query, "type": "cells"}]

    def get_item(self, item_id):
        self.get_item_calls.append(item_id)
        return {
            "item_id": item_id,
            "name": "cell one",
            "type": "cells",
            "characteristic_chemical_formula": "LiCoO2",
            "last_modified": "2026-02-02",
            "positive_electrode": [{"item": {"name": "NMC811"}}],
            "negative_electrode": [{"item": {"chemform": "", "name": "Li"}}],
            "electrolyte": [{"item": {"name": "LP30"}}],
        }


class _FakeClient:
    """Stand-in for DatalabPlotClient (passed in, so it is never closed)."""

    def __init__(self):
        self.client = _FakeInner()


def test_find_cells_no_query_dedups_across_types():
    df = find_cells(client=_FakeClient(), enrich=False)
    assert list(df.columns) == EXPECTED_COLUMNS
    # get_items returns the same row for both "samples" and "cells" -> deduped.
    assert len(df) == 1
    assert df.iloc[0]["item_id"] == "CEL-1"


def test_find_cells_enrich_populates_construction_fields():
    fake = _FakeClient()
    df = find_cells(client=fake, enrich=True)
    row = df.iloc[0]
    # Construction fields come only from the per-cell get_item fetch.
    assert fake.client.get_item_calls == ["CEL-1"]
    assert row["positive_electrode"] == "NMC811"
    assert row["negative_electrode"] == "Li"
    assert row["electrolyte"] == "LP30"
    assert row["last_modified"] == "2026-02-02"  # from the full item


def test_find_cells_enrich_false_skips_per_item_fetch():
    fake = _FakeClient()
    df = find_cells(client=fake, enrich=False)
    row = df.iloc[0]
    assert fake.client.get_item_calls == []
    # Construction fields blank without enrichment; summary fields still set.
    assert row["positive_electrode"] == ""
    assert row["chemform"] == "LiCoO2"
    assert row["last_modified"] == "2026-01-01"  # the summary `date`


def test_find_cells_with_query_uses_search():
    fake = _FakeClient()
    df = find_cells("LTO", client=fake, enrich=False)
    assert len(df) == 1
    assert df.iloc[0]["item_id"] == "CEL-9"
    assert fake.client.search_calls == [("LTO", ("samples", "cells"))]


def test_find_cells_no_query_sorts_most_recent_first():
    class _MultiInner:
        def get_items(self, item_type):
            return {
                "samples": [
                    {"item_id": "OLD", "type": "samples", "date": "2026-01-01"},
                    {"item_id": "NEW", "type": "samples", "date": "2026-09-09"},
                    {"item_id": "MID", "type": "samples", "last_modified": "2026-05-05"},
                ]
            }

    class _C:
        client = _MultiInner()

    df = find_cells(client=_C(), item_type="samples", enrich=False)
    assert list(df["item_id"]) == ["NEW", "MID", "OLD"]
    # `limit` then yields the newest N.
    df2 = find_cells(client=_C(), item_type="samples", limit=2, enrich=False)
    assert list(df2["item_id"]) == ["NEW", "MID"]
