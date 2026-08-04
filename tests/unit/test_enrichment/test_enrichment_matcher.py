# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the enrichment matcher SQL query builder.

The matcher receives an already-resolved SQL expression (``match_expr``) plus a
``value_kind`` that says how to read values out of it:
- ``array``       — a native text[] column, read element-wise via ``unnest``
- ``jsonb_array`` — a jsonb array, read element-wise via ``jsonb_array_elements_text``
- ``scalar``      — a single value (column, jsonb ->> path, or COALESCE), cast ::text

The resolved expression is produced by FieldResolver and is therefore trusted/
escaped — the matcher interpolates it directly. CIDR is a special exact-on-ip mode.
"""

import pytest

from src.modules.enrichment.domain.enrichment_matcher import (
    build_match_and_apply_query,
    build_preview_query,
    build_count_query,
)


SAMPLE_DATASET_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# Resolved expressions the FieldResolver would emit for representative fields.
HOSTNAME_EXPR = "hostname"
SN_EXPR = "sn"
IP_EXPR = "ip"
EXT_EXPR = "extended_attributes->'SentinelOne'->>'siteName'"
GROUP_EXPR = (
    "COALESCE(extended_attributes->'Entra A'->>'building', "
    "extended_attributes->'Entra B'->>'building')"
)
JSONB_ARRAY_EXPR = "extended_attributes->'AssetAtlas'->'ip_private'"


class TestArrayMatch:
    """Native text[] columns match element-wise via unnest."""

    def test_apply_uses_unnest_on_expr(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact", "location"
        )
        assert "unnest(hostname)" in sql

    def test_apply_uses_lower_comparison_on_av_val(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact", "location"
        )
        assert "LOWER(av.val) = LOWER(er.match_value)" in sql

    def test_apply_merges_into_enrichment_namespace(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact", "building_code"
        )
        assert "extended_attributes" in sql
        assert "'enrichment'" in sql
        assert "'building_code'" in sql

    def test_apply_params_and_placeholder(self):
        sql, params = build_match_and_apply_query(
            SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact", "location"
        )
        assert params["dataset_id"] == SAMPLE_DATASET_ID
        assert "%(dataset_id)s" in sql

    def test_apply_returns_asset_ids(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact", "location"
        )
        assert "RETURNING a.id::text" in sql

    def test_sn_array_match_uses_unnest(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, SN_EXPR, "array", "exact", "warranty_status"
        )
        assert "unnest(sn)" in sql


class TestScalarExtMatch:
    """Single-valued jsonb paths (ext.<source>.<key>) match the whole value."""

    def test_apply_uses_jsonb_path_expr(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, EXT_EXPR, "scalar", "exact", "entity"
        )
        assert "extended_attributes->'SentinelOne'->>'siteName'" in sql

    def test_apply_does_not_unnest(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, EXT_EXPR, "scalar", "exact", "entity"
        )
        assert "unnest" not in sql

    def test_apply_casts_to_text(self):
        # Cast keeps the comparison valid for non-text scalar columns too.
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, EXT_EXPR, "scalar", "exact", "entity"
        )
        assert "::text" in sql

    def test_apply_uses_lower_comparison_on_av_val(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, EXT_EXPR, "scalar", "exact", "entity"
        )
        assert "LOWER(av.val) = LOWER(er.match_value)" in sql

    def test_apply_writes_target_field(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, EXT_EXPR, "scalar", "exact", "entity_name"
        )
        assert "'entity_name'" in sql


class TestScalarIntegrationGroupMatch:
    """The bug fix: a derived COALESCE field (integration group) is matchable."""

    def test_apply_uses_coalesce_expr(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, GROUP_EXPR, "scalar", "exact", "site"
        )
        assert GROUP_EXPR in sql

    def test_apply_does_not_unnest_coalesce(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, GROUP_EXPR, "scalar", "exact", "site"
        )
        assert "unnest" not in sql

    def test_count_uses_coalesce_expr(self):
        sql, _ = build_count_query(SAMPLE_DATASET_ID, GROUP_EXPR, "scalar", "exact")
        assert GROUP_EXPR in sql


class TestJsonbArrayMatch:
    """jsonb arrays (e.g. core-enrichment Private IP) match element-wise."""

    def test_apply_uses_jsonb_array_elements_text(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, JSONB_ARRAY_EXPR, "jsonb_array", "exact", "tier"
        )
        assert f"jsonb_array_elements_text({JSONB_ARRAY_EXPR})" in sql

    def test_apply_uses_lower_comparison(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, JSONB_ARRAY_EXPR, "jsonb_array", "exact", "tier"
        )
        assert "LOWER(av.val) = LOWER(er.match_value)" in sql

    def test_apply_does_not_use_column_unnest(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, JSONB_ARRAY_EXPR, "jsonb_array", "exact", "tier"
        )
        assert "unnest(" not in sql


class TestCidrMatch:
    """CIDR mode is exact-on-ip with inet containment, most-specific first."""

    def test_apply_uses_inet_containment(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, IP_EXPR, "array", "cidr", "subnet_name"
        )
        assert "<<=" in sql
        assert "inet" in sql.lower()

    def test_apply_unnests_ip(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, IP_EXPR, "array", "cidr", "subnet_name"
        )
        assert "unnest(ip)" in sql

    def test_apply_orders_by_masklen_desc(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, IP_EXPR, "array", "cidr", "subnet_name"
        )
        assert "masklen" in sql
        assert "DESC" in sql


class TestPreviewQuery:
    """Preview returns a sample of matches; select, not update."""

    def test_preview_is_select_not_update(self):
        sql, _ = build_preview_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert "SELECT" in sql
        assert "UPDATE" not in sql

    def test_preview_has_limit(self):
        sql, _ = build_preview_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert "LIMIT" in sql

    def test_preview_array_uses_lower(self):
        sql, _ = build_preview_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert "LOWER(av.val) = LOWER(er.match_value)" in sql

    def test_preview_scalar_uses_expr(self):
        sql, _ = build_preview_query(SAMPLE_DATASET_ID, EXT_EXPR, "scalar", "exact")
        assert "extended_attributes->'SentinelOne'->>'siteName'" in sql

    def test_preview_cidr_uses_inet(self):
        sql, _ = build_preview_query(SAMPLE_DATASET_ID, IP_EXPR, "array", "cidr")
        assert "inet" in sql.lower()
        assert "<<=" in sql

    def test_preview_params(self):
        _, params = build_preview_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert params["dataset_id"] == SAMPLE_DATASET_ID


class TestCountQuery:
    """Count returns the number of distinct matched assets."""

    def test_count_uses_count_distinct(self):
        sql, _ = build_count_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert "COUNT(DISTINCT a.id)" in sql

    def test_count_is_select_not_update(self):
        sql, _ = build_count_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert "SELECT" in sql
        assert "UPDATE" not in sql

    def test_count_array_uses_lower(self):
        sql, _ = build_count_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert "LOWER(av.val) = LOWER(er.match_value)" in sql

    def test_count_cidr_uses_inet(self):
        sql, _ = build_count_query(SAMPLE_DATASET_ID, IP_EXPR, "array", "cidr")
        assert "inet" in sql.lower()
        assert "<<=" in sql

    def test_count_params(self):
        _, params = build_count_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "array", "exact")
        assert params["dataset_id"] == SAMPLE_DATASET_ID


class TestDeterministicTiebreaker:
    """DISTINCT ON (a.id) must break ties deterministically (#801).

    When one asset matches multiple enrichment rows with DIFFERENT enrich_values
    (e.g. a device in two SentinelOne sites mapped to two legal entities),
    ``ORDER BY a.id`` alone leaves the surviving row to physical scan order, so
    the applied value flips between runs -> ~8k spurious attribute_updated events.
    A value tiebreaker after a.id makes the winner stable and reproducible.
    """

    def _order_clause(self, sql: str) -> str:
        assert "ORDER BY" in sql
        return sql[sql.index("ORDER BY"):]

    def test_apply_exact_breaks_ties_on_value(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, GROUP_EXPR, "scalar", "exact", "legal_entity"
        )
        order = self._order_clause(sql)
        assert "a.id" in order
        assert "er.enrich_value" in order
        assert "er.match_value" in order

    def test_apply_cidr_keeps_masklen_first_then_value(self):
        sql, _ = build_match_and_apply_query(
            SAMPLE_DATASET_ID, IP_EXPR, "array", "cidr", "subnet_name"
        )
        order = self._order_clause(sql)
        # most-specific CIDR still wins, but ties below it are now deterministic
        assert order.index("masklen") < order.index("er.enrich_value")
        assert "DESC" in order
        assert "er.match_value" in order

    def test_preview_uses_same_tiebreaker_as_apply(self):
        # Preview must show the value the apply will actually write.
        sql, _ = build_preview_query(SAMPLE_DATASET_ID, GROUP_EXPR, "scalar", "exact")
        order = self._order_clause(sql)
        assert "er.enrich_value" in order
        assert "er.match_value" in order


class TestUnknownValueKind:
    """A bad value_kind is a programming error, surfaced loudly."""

    def test_unknown_value_kind_raises(self):
        with pytest.raises(ValueError):
            build_count_query(SAMPLE_DATASET_ID, HOSTNAME_EXPR, "bogus", "exact")
