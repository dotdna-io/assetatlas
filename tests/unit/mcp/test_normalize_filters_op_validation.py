# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase 3 + Phase 0: the MCP filter path must validate operators AND fields
against the registry/catalog, raising (never dropping) on an unresolvable one.

WHY (spec §6.2 / R5 / §4): the MCP path historically dropped unknown operators
AND unknown fields — either fell through/was skipped and silently WIDENED the
result set without the LLM knowing (a correctness AND over-disclosure risk: a
dropped filter returns MORE rows than asked). New ops (all/none/between/
within_last) must round-trip with canonical codes; a typo'd op raises
OperatorResolutionError; an unresolvable field raises FieldResolutionError. The
boundary functions surface the message through their existing string error
channel (the MCP no-exceptions convention) so the chat surface can self-correct.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.mcp.tools.assets import (
    FieldResolutionError,
    _normalize_filters,
    list_assets_query,
    list_assets_impl,
    aggregate_assets_query,
    get_field_values_impl,
)


def test_known_ops_all_none_pass_through():
    out = _normalize_filters([
        {"field": "data_source", "op": "all", "value": ["jamf", "sccm"]},
        {"field": "data_source", "op": "none", "value": ["sccm"]},
    ])
    assert [f["op"] for f in out] == ["all", "none"]


def test_known_new_ops_between_within_last_pass_through():
    out = _normalize_filters([
        {"field": "last_seen", "op": "between", "value": ["30d", "7d"]},
        {"field": "last_seen", "op": "within_last", "value": "30d"},
    ])
    assert [f["op"] for f in out] == ["between", "within_last"]


def test_alias_op_normalized_to_canonical_code():
    # An alias spelling is normalised to its canonical code before the repo
    # sees it, so the repo never has to re-resolve a label.
    out = _normalize_filters([
        {"field": "category", "op": "is_empty", "value": None},
        {"field": "category", "op": "any of", "value": ["a", "b"]},
    ])
    assert [f["op"] for f in out] == ["empty", "any"]


def test_unknown_op_is_rejected_not_dropped():
    """An unknown operator raises (loud) rather than being silently dropped —
    a dropped filter would widen the result set without the user knowing."""
    with pytest.raises(Exception) as exc:
        _normalize_filters([
            {"field": "data_source", "op": "haz", "value": ["jamf"]},
        ])
    # The message names the bad operator so the LLM can self-correct.
    assert "haz" in str(exc.value)


def test_all_none_flow_reaches_repo_with_canonical_codes():
    """all/none survive normalization and reach repo.list_assets with canonical
    codes + list values (the §12-mandated MCP→repo flow)."""
    db = MagicMock()
    resolver = MagicMock()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        list_assets_query(
            db, resolver,
            filters=[
                {"field": "data_source", "op": "all", "value": ["jamf", "sccm"]},
                {"field": "data_source", "op": "none", "value": ["sccm"]},
            ],
        )
        passed = MockRepo.return_value.list_assets.call_args.kwargs["filters"]
        assert [f["op"] for f in passed] == ["all", "none"]
        assert passed[0]["value"] == ["jamf", "sccm"]


def test_unknown_op_surfaced_as_string_by_list_assets_impl():
    """list_assets_impl converts the raised error into an LLM-facing string
    (MCP boundary convention: no exceptions cross the tool boundary)."""
    db = MagicMock()
    resolver = MagicMock()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        out = list_assets_impl(
            db, resolver,
            filters=[{"field": "category", "op": "haz", "value": "x"}],
        )
    assert isinstance(out, str)
    assert "haz" in out


def test_unknown_op_surfaced_as_hint_by_aggregate_query():
    db = MagicMock()
    resolver = MagicMock()
    with patch("src.mcp.tools.assets.AssetRepository"):
        result, hint = aggregate_assets_query(
            db, resolver,
            row_field="category",
            filters=[{"field": "category", "op": "haz", "value": "x"}],
        )
    assert result is None
    assert hint and "haz" in hint


def test_unknown_op_surfaced_as_string_by_get_field_values_impl():
    db = MagicMock()
    resolver = MagicMock()
    with patch("src.mcp.tools.assets.AssetRepository"):
        out = get_field_values_impl(
            db, resolver,
            field="category",
            filters=[{"field": "category", "op": "haz", "value": "x"}],
        )
    assert isinstance(out, str)
    assert "haz" in out


# ---------------------------------------------------------------------------
# P5: catalog-aware field resolution in the filter path
# ---------------------------------------------------------------------------

def test_normalize_filters_catalog_field_resolution():
    """Catalog-aware field in filter is resolved via resolver, not dropped.

    WHY (#735 P5): data-source-specific fields (e.g. "Beszel - cpu") are
    unknown to the static/virtual alias tables, so they were silently dropped
    before this fix. The resolver's catalog can resolve them to a FieldId;
    _normalize_filters must fall through to that lookup before dropping.
    """
    from src.modules.fields.models import IntegrationGroupField

    field_id_obj = IntegrationGroupField(
        kind="integration_group", group="Beszel", attribute="cpu"
    )
    catalog_mock = MagicMock()
    catalog_mock.field_id_for_display_name.return_value = field_id_obj
    resolver_mock = MagicMock()
    resolver_mock.catalog.return_value = catalog_mock

    filters = [{"field": "Beszel - cpu", "op": "eq", "value": "x"}]
    result = _normalize_filters(filters, resolver=resolver_mock)

    assert result is not None
    assert len(result) == 1
    assert result[0]["field_id"] == {
        "kind": "integration_group", "group": "Beszel", "attribute": "cpu"
    }


def test_normalize_filters_catalog_miss_raises():
    """If the resolver catalog can't resolve the field, raise (don't drop).

    WHY (§4 loud-error-over-silent-drop): a dropped filter silently WIDENS the
    result — the user asked to narrow by a field and instead got the whole set
    back (correctness + over-disclosure). Raising lets the chat boundary tell
    the model the field is unknown so it can self-correct.
    """
    catalog_mock = MagicMock()
    catalog_mock.field_id_for_display_name.return_value = None
    resolver_mock = MagicMock()
    resolver_mock.catalog.return_value = catalog_mock

    filters = [{"field": "totally_unknown_field", "op": "eq", "value": "x"}]
    with pytest.raises(FieldResolutionError) as exc:
        _normalize_filters(filters, resolver=resolver_mock)
    # The message names the bad field so the LLM can self-correct.
    assert "totally_unknown_field" in str(exc.value)


def test_normalize_filters_no_resolver_unknown_field_raises():
    """Without a resolver, an unknown field still raises (not dropped)."""
    filters = [{"field": "totally_unknown_field", "op": "eq", "value": "x"}]
    with pytest.raises(FieldResolutionError):
        _normalize_filters(filters)


def test_normalize_filters_static_still_resolves_without_catalog():
    """Static fields still resolve when resolver=None (regression guard)."""
    filters = [{"field": "category", "op": "eq", "value": "Server"}]
    result = _normalize_filters(filters)
    assert result is not None
    assert len(result) == 1
    assert result[0]["field_id"] == {"kind": "static", "name": "Category"}


def test_normalize_filters_encoded_field_id_string_resolves():
    """#735 review: an already-encoded field id (e.g. from list_fields) passed
    as a filter field STRING must decode to the repo dict, not be silently
    dropped (symmetry with _normalize_field_name's encoded passthrough)."""
    filters = [{"field": "ext:Beszel:cpu", "op": "eq", "value": "x"}]
    result = _normalize_filters(filters)
    assert result is not None
    assert result[0]["field_id"] == {
        "kind": "extended_attribute", "source": "Beszel", "attribute": "cpu",
    }


def test_normalize_filters_malformed_encoded_field_raises():
    """A malformed encoded field id raises — same loud-error contract as an
    unknown bare field. WHY: an undecodable ``ext:onlyonesegment`` is just as
    unresolvable; dropping it would silently widen the result set (§4)."""
    filters = [{"field": "ext:onlyonesegment", "op": "eq", "value": "x"}]
    with pytest.raises(FieldResolutionError):
        _normalize_filters(filters)


# ---------------------------------------------------------------------------
# Phase 0: unresolvable sort_by raises (was: silently sorted unsorted)
# ---------------------------------------------------------------------------

def test_list_assets_query_unresolvable_sort_by_raises():
    """An unresolvable ``sort_by`` RAISES rather than silently sorting in
    default order. WHY (§4): the old code dropped the bad sort and returned rows
    in DEFAULT order — a DIFFERENT result than asked, with no signal. The repo
    must never even run, so no wrongly-ordered set can come back as if the sort
    had succeeded."""
    db = MagicMock()
    resolver = MagicMock()
    resolver.catalog.return_value.field_id_for_display_name.return_value = None
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([{"hostname": "h"}], 1)
        with pytest.raises(FieldResolutionError) as exc:
            list_assets_query(db, resolver, sort_by="totally_unknown_field")
        MockRepo.return_value.list_assets.assert_not_called()
    assert "totally_unknown_field" in str(exc.value)


def test_list_assets_impl_unresolvable_sort_by_surfaces_error_not_rows():
    """list_assets_impl converts the sort raise into an LLM-facing string and
    does NOT fall through to a rows summary — proving the failure is observable,
    not swallowed into a silently-unsorted result."""
    db = MagicMock()
    resolver = MagicMock()
    resolver.catalog.return_value.field_id_for_display_name.return_value = None
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([{"hostname": "h"}], 1)
        out = list_assets_impl(db, resolver, sort_by="totally_unknown_field")
    assert isinstance(out, str)
    assert "totally_unknown_field" in out
    # A successful list begins with "Assets: N total"; its absence proves the
    # bad sort was NOT silently swallowed and rows returned in the wrong order.
    assert "Assets:" not in out


def test_list_assets_query_encoded_dead_sort_by_raises():
    """An ENCODED but dead sort_by ('static:Bogus' decodes fine but isn't a live
    field) raises FieldResolutionError here, instead of reaching the repo's
    ORDER BY as an uncaught InvalidFieldError. _normalize_field_name passes
    encoded ids through unvalidated, so list_assets_query must catalog-check it."""
    from src.modules.fields.models import InvalidFieldError
    db = MagicMock()
    resolver = MagicMock()
    resolver.resolve.side_effect = InvalidFieldError("not a live field")
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.list_assets.return_value = ([], 0)
        with pytest.raises(FieldResolutionError) as exc:
            list_assets_query(db, resolver, sort_by="static:Bogus")
        MockRepo.return_value.list_assets.assert_not_called()
    assert "static:Bogus" in str(exc.value)
