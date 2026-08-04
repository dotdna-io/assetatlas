# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke tests for the asset *_impl helpers.

Each test patches AssetRepository so the impl can be exercised without a DB.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.mcp.tools.assets import (
    aggregate_assets_impl,
    aggregate_assets_query,
    format_aggregate_result,
    get_asset_impl,
    get_field_values_impl,
    list_assets_impl,
    search_assets_impl,
)


def _make_resolver():
    r = MagicMock()
    r.catalog.return_value.field_id_for_display_name.return_value = None
    return r


def test_search_assets_impl_calls_repo_and_returns_marker():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        repo = MockRepo.return_value
        repo.search_assets.return_value = [
            {"id": "a-1", "hostname": ["host01"], "category": "server",
             "match_field": "hostname"},
        ]
        out = search_assets_impl(db, resolver, query="host01", limit=5)
    repo.search_assets.assert_called_once_with("host01", limit=5)
    assert "host01" in out


def test_get_asset_impl_returns_asset_summary():
    db = MagicMock()
    resolver = _make_resolver()
    aid = str(uuid4())
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        repo = MockRepo.return_value
        repo.get_asset_by_id.return_value = {
            "id": aid,
            "hostname": ["host01"],
            "category": "server",
            "subcategory": "linux",
            "confidence": 0.85,
        }
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = []
        out = get_asset_impl(db, resolver, asset_id=aid)
    assert aid in out
    assert "host01" in out
    assert "server" in out


def test_list_assets_impl_returns_list():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        repo = MockRepo.return_value
        repo.list_assets.return_value = (
            [{"id": "a-1", "hostname": ["host01"], "category": "server"}],
            1,
        )
        out = list_assets_impl(db, resolver, page=1, limit=10)
    assert "Assets:" in out
    assert "host01" in out


def test_list_assets_impl_formats_encoded_fieldid_rows():
    """repo.list_assets() returns dicts keyed on encoded FieldIds
    ('static:Hostname'), not snake_case. The impl must project those back
    to the keys the formatter expects — otherwise every row prints
    '(no hostname)' and the LLM is forced to loop get_asset per id."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = (
            [{
                "id": "a-1",
                "static:Hostname": ["gs-win-01"],
                "static:Category": "Server",
                "static:OS%20Normalized": "windows server 2022",
                "static:Owner": "golden-seed@test",
                "static:Location": "lodz",
                "static:Confidence%20Score": 0.99,
            }],
            1,
        )
        out = list_assets_impl(db, resolver, search="lodz")
    # Core identity
    assert "gs-win-01" in out
    assert "Server" in out
    # Ownership / location show inline so the model doesn't have to
    # call get_asset for each row.
    assert "golden-seed@test" in out
    assert "lodz" in out
    assert "windows server 2022" in out
    assert "(no hostname)" not in out


def test_aggregate_assets_impl_renders_1d_pivot():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.aggregate_assets.return_value = {
            "rows": ["server", "workstation"],
            "columns": [],
            "data": {"server": {"count": 5}, "workstation": {"count": 3}},
            "grand_total": 8,
        }
        out = aggregate_assets_impl(db, resolver, row_field="category")
    # Output leads with the grand-total on its own line so a weak LLM
    # doesn't have to parse the header; the breakdown follows.
    assert "COUNT: 8 assets match the filter." in out
    assert "Breakdown by category" in out
    assert "server" in out
    assert "workstation" in out


def test_get_field_values_impl_lists_values():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.get_field_values.return_value = (
            [{"value": "server", "count": 12},
             {"value": "workstation", "count": 4}],
            2,
        )
        out = get_field_values_impl(db, resolver, field="category")
    assert "category" in out
    assert "server" in out
    assert "12" in out


def test_list_assets_impl_normalizes_field_and_field_id_filter_keys():
    """Both the documented `field` key and the LLM's common `field_id` key
    (copied from semantic_suggest output) should normalize to the repo's
    `{field_id: {kind, name}}` shape, with the bare name title-cased."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        list_assets_impl(
            db, resolver,
            filters=[
                {"field": "hostname", "op": "eq", "value": "host01"},
                {"field_id": "owner", "op": "contains", "value": "Eric"},
            ],
            limit=5,
        )
    passed = MockRepo.return_value.list_assets.call_args[1]["filters"]
    assert passed == [
        {"field_id": {"kind": "static", "name": "Hostname"},
         "op": "eq", "value": "host01"},
        {"field_id": {"kind": "static", "name": "Owner"},
         "op": "contains", "value": "Eric"},
    ]


def test_list_assets_impl_surfaces_unknown_filter_field_error():
    """An unknown filter field is rejected loudly (a string the LLM can act on),
    NOT dropped — a dropped filter would silently widen the result set, which is
    a correctness AND over-disclosure risk (§4). The repo must never run, so no
    widened (effectively unfiltered) set comes back."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        out = list_assets_impl(
            db, resolver,
            filters=[{"field": "not_a_real_field", "op": "eq", "value": "x"}],
        )
    assert isinstance(out, str)
    assert "not_a_real_field" in out
    MockRepo.return_value.list_assets.assert_not_called()


def test_list_assets_impl_normalizes_bare_field_names_in_fields_arg():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        list_assets_impl(
            db, resolver,
            fields="hostname,os_normalized,bogus_field",
        )
    passed = MockRepo.return_value.list_assets.call_args[1]["fields"]
    # bare names get `static:` + title-cased display name; unknowns dropped.
    assert passed == ["static:Hostname", "static:OS%20Normalized"]


def test_list_assets_impl_normalizes_sort_by():
    """sort_by must go through the same display-name / snake-case coercion
    that filters use, or sorting on 'OS Normalized' / 'os_normalized' breaks."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        list_assets_impl(db, resolver, sort_by="os_normalized")
    passed = MockRepo.return_value.list_assets.call_args[1]["sort_by"]
    assert passed == "static:OS%20Normalized"


def test_list_assets_impl_surfaces_unresolvable_sort_by_error():
    """Unknown sort_by is rejected loudly, NOT dropped to None — silently
    sorting in default order returns a different result than the user asked for,
    with no signal (§4). The repo must never run."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        out = list_assets_impl(db, resolver, sort_by="not_a_real_field")
    assert isinstance(out, str)
    assert "not_a_real_field" in out
    MockRepo.return_value.list_assets.assert_not_called()


def test_aggregate_assets_impl_normalizes_display_name_row_field():
    """A bare display name like 'OS Normalized' must be encoded to the
    resolver's expected form before hitting the repo — otherwise
    decode_field_id() rejects it and the whole query silently returns empty."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.aggregate_assets.return_value = {
            "rows": ["Windows 11"], "columns": [],
            "data": {"Windows 11": {"count": 42}}, "grand_total": 42,
        }
        aggregate_assets_impl(db, resolver, row_field="OS Normalized")
    passed = MockRepo.return_value.aggregate_assets.call_args[1]
    assert passed["row_field"] == "static:OS%20Normalized"


def test_aggregate_assets_impl_normalizes_snake_case_row_field():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.aggregate_assets.return_value = {
            "rows": [], "columns": [], "data": {}, "grand_total": 0,
        }
        aggregate_assets_impl(db, resolver, row_field="os_normalized")
    passed = MockRepo.return_value.aggregate_assets.call_args[1]
    assert passed["row_field"] == "static:OS%20Normalized"


def test_aggregate_assets_impl_passes_encoded_field_through_unchanged():
    """If the caller already knows the encoded form, keep it."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.aggregate_assets.return_value = {
            "rows": [], "columns": [], "data": {}, "grand_total": 0,
        }
        aggregate_assets_impl(db, resolver,
                              row_field="ext:Entra Devices:deviceOwnership")
    passed = MockRepo.return_value.aggregate_assets.call_args[1]
    assert passed["row_field"] == "ext:Entra Devices:deviceOwnership"


def test_aggregate_assets_impl_unknown_row_field_returns_hint():
    """Unknown bare fields get a helpful pointer to semantic_suggest — NOT a
    silent 'no data found' that triggers the model to retry endlessly."""
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        out = aggregate_assets_impl(db, resolver,
                                    row_field="definitely_not_a_field")
    assert "definitely_not_a_field" in out
    assert "semantic_suggest" in out
    # Must not have called the repo — we bailed before building SQL.
    MockRepo.return_value.aggregate_assets.assert_not_called()


def test_aggregate_assets_impl_normalizes_col_field():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.aggregate_assets.return_value = {
            "rows": ["x"], "columns": ["y"],
            "data": {"x": {"y": 1}}, "grand_total": 1,
        }
        aggregate_assets_impl(db, resolver,
                              row_field="category",
                              col_field="OS Normalized")
    passed = MockRepo.return_value.aggregate_assets.call_args[1]
    assert passed["row_field"] == "static:Category"
    assert passed["col_field"] == "static:OS%20Normalized"


def test_get_field_values_impl_normalizes_field():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.get_field_values.return_value = ([], 0)
        get_field_values_impl(db, resolver, field="OS Normalized")
    passed = MockRepo.return_value.get_field_values.call_args[1]
    assert passed["field"] == "static:OS%20Normalized"


def test_get_field_values_impl_unknown_field_returns_hint():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        out = get_field_values_impl(db, resolver, field="bogus_field")
    assert "bogus_field" in out
    assert "semantic_suggest" in out
    MockRepo.return_value.get_field_values.assert_not_called()


# --- aggregate_assets_query / format_aggregate_result split --------------
# These lock the contract the chat binding relies on to build a donut
# artifact (raw repo dict) alongside the LLM summary (formatted string).

def test_aggregate_assets_query_returns_raw_result_on_success():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.aggregate_assets.return_value = {
            "rows": ["server", "workstation"],
            "columns": [],
            "data": {"server": {"count": 5}, "workstation": {"count": 3}},
            "grand_total": 8,
        }
        result, err = aggregate_assets_query(
            db, resolver, row_field="category",
        )
    assert err is None
    assert result is not None
    assert result["grand_total"] == 8
    assert result["rows"] == ["server", "workstation"]


def test_aggregate_assets_query_unknown_field_returns_hint_not_result():
    db = MagicMock()
    resolver = _make_resolver()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        result, err = aggregate_assets_query(
            db, resolver, row_field="definitely_not_a_field",
        )
    assert result is None
    assert err is not None
    assert "definitely_not_a_field" in err
    assert "semantic_suggest" in err
    MockRepo.return_value.aggregate_assets.assert_not_called()


def test_format_aggregate_result_1d_matches_impl_output():
    raw = {
        "rows": ["server", "workstation"],
        "columns": [],
        "data": {"server": {"count": 5}, "workstation": {"count": 3}},
        "grand_total": 8,
    }
    out = format_aggregate_result(raw, row_field="category")
    assert "COUNT: 8 assets match the filter." in out
    assert "Breakdown by category" in out
    assert "server" in out
    assert "workstation" in out


def test_format_aggregate_result_empty_rows_returns_no_data_message():
    raw = {"rows": [], "columns": [], "data": {}, "grand_total": 0}
    out = format_aggregate_result(raw, row_field="category")
    assert "No data found" in out
    assert "category" in out


def test_render_row_value_formats_decimal_confidence_as_percent():
    """confidence is NUMERIC(3,2) -> psycopg2 returns a Decimal, which is neither
    int nor float; it must still render as a percent ('85%'), not the raw '0.85'
    that the int/float-only check produced."""
    from decimal import Decimal
    from src.mcp.tools.assets import _render_row_value

    assert _render_row_value("confidence", Decimal("0.85")) == "85%"
    assert _render_row_value("confidence", 0.92) == "92%"   # float still works
