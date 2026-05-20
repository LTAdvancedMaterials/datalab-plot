"""Tests for datalab_plot.search — constituent formatting and item discovery."""
from __future__ import annotations

from datalab_plot.search import _format_constituents, _row_from_item, find_cells

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


def test_format_constituents_empty_or_invalid():
    assert _format_constituents([]) == ""
    assert _format_constituents(None) == ""
    assert _format_constituents("not a list") == ""


def test_row_from_item_shape():
    row = _row_from_item(
        {
            "item_id": "CEL-1",
            "name": "test cell",
            "type": "cells",
            "collections": [{"collection_id": "proj-A"}],
        }
    )
    assert set(row) == set(EXPECTED_COLUMNS)
    assert row["item_id"] == "CEL-1"
    assert row["collections"] == "proj-A"


class _FakeInner:
    """Stand-in for datalab_api.DatalabClient."""

    def __init__(self):
        self.search_calls: list = []

    def get_items(self, item_type):
        return [{"item_id": "CEL-1", "name": "cell one", "type": "cells"}]

    def search_items(self, query, item_types):
        self.search_calls.append((query, item_types))
        return [{"item_id": "CEL-9", "name": query, "type": "cells"}]


class _FakeClient:
    """Stand-in for DatalabPlotClient (passed in, so it is never closed)."""

    def __init__(self):
        self.client = _FakeInner()


def test_find_cells_no_query_dedups_across_types():
    df = find_cells(client=_FakeClient())
    assert list(df.columns) == EXPECTED_COLUMNS
    # get_items returns the same row for both "samples" and "cells" -> deduped.
    assert len(df) == 1
    assert df.iloc[0]["item_id"] == "CEL-1"


def test_find_cells_with_query_uses_search():
    fake = _FakeClient()
    df = find_cells("LTO", client=fake)
    assert len(df) == 1
    assert df.iloc[0]["item_id"] == "CEL-9"
    assert fake.client.search_calls == [("LTO", ("samples", "cells"))]
