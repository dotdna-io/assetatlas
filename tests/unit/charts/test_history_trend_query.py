# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for QueryBuilder._build_history_trend_query (#497/#946).

Snapshot-history time-series over assets_history: buckets the LAST snapshot
per DATE_TRUNC(interval, archived_at), counts matching rows, and appends a
live 'now' bucket from `assets`. Rows are shaped {timestamp, value, series?}
— identical to the `trend` type so downstream transforms reuse it unchanged.

These are SQL-substring / params assertions (no live DB); real-DB validation
happens in a later task.
"""

import importlib
from unittest.mock import MagicMock

import pytest

_m = importlib.import_module("src.api.blueprints.charts.models")
ChartQueryRequest = _m.ChartQueryRequest
from src.modules.charts.services.query_builder import QueryBuilder
from src.modules.fields import FieldCatalog, FieldResolver


def _resolver(tag_names=None, posture_categories=None):
    db = MagicMock()
    tag_rows = [{"tag_name": t} for t in (tag_names or [])]
    posture_rows = [{"id": c} for c in (posture_categories or [])]

    def _execute_query(sql, params):
        s = sql.strip()
        if "asset_tags_v2" in s or "tag_name" in s:
            return tag_rows
        if "posture_categories" in s:
            return posture_rows
        return []

    db.execute_query.side_effect = _execute_query
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _builder(tag_names=None, posture_categories=None):
    return QueryBuilder(
        resolver=_resolver(tag_names=tag_names, posture_categories=posture_categories)
    )


def _req(**over):
    payload = {
        "queryType": "history_trend",
        "from": {"table": "assets", "joins": []},
        "select": {}, "filters": [],
        "options": {"interval": "day", "last": "30d"},
    }
    payload.update(over)
    return ChartQueryRequest.model_validate(payload)


def test_count_buckets_from_history():
    sql, params = _builder()._build_history_trend_query(_req())
    assert "assets_history" in sql
    assert "DATE_TRUNC('day'" in sql
    assert "generation_run_id" in sql
    assert "archived_at" in sql
    # last snapshot per bucket
    assert "ROW_NUMBER() OVER" in sql and "rn = 1" in sql.replace(" ", "").replace("\n", "") or "rn=1" in sql.replace(" ", "").replace("\n", "")


def test_count_appends_live_now_point():
    sql, _ = _builder()._build_history_trend_query(_req())
    assert "UNION ALL" in sql
    assert "FROM assets" in sql  # the live "now" leg
    assert "NOW()" in sql


def test_range_filter_applied():
    sql, params = _builder()._build_history_trend_query(_req())
    assert "30 days" in sql


def test_no_range_when_all_time():
    req = _req(options={"interval": "week"})
    sql, _ = _builder()._build_history_trend_query(req)
    assert "DATE_TRUNC('week'" in sql
    # All-time (no `last`) must not emit a range predicate on the history leg.
    assert "archived_at >=" not in sql


def test_split_dimension_emits_series():
    req = _req(select={"dimension": {"kind": "static", "name": "Category"}})
    sql, _ = _builder()._build_history_trend_query(req)
    assert "AS series" in sql
    assert sql.count("GROUP BY") >= 2  # both legs group by bucket + series


def test_percentage_mode_uses_filter_clauses():
    req = _req(options={"interval": "day", "last": "30d", "showPercentage": True})
    sql, _ = _builder()._build_history_trend_query(req)
    assert "FILTER (WHERE" in sql or "* 100.0" in sql
    assert "ROUND(" in sql


def test_split_respects_dimension_limit():
    req = _req(
        select={"dimension": {"kind": "static", "name": "Category"}},
        options={"interval": "day", "last": "30d", "dimensionLimit": 5},
    )
    sql, params = _builder()._build_history_trend_query(req)
    # top-N dimension cap by current live count, applied to both legs
    assert "LIMIT %(dimension_limit)s" in sql
    assert params["dimension_limit"] == 5


def test_history_trend_os_eol_filter_now_supported():
    """OS EOL Status is archived into assets_history as of migration 136, so a
    historical filter on it now builds valid SQL against the archived column
    instead of failing loud (#1024)."""
    req = _req(filters=[{
        "field_id": {"kind": "static", "name": "OS EOL Status"},
        "operator": "equals",
        "value": "active",
    }])
    sql, _ = _builder().build_query(req)
    assert "assets_history" in sql
    assert "os_eol_status" in sql


def test_history_trend_os_eol_dimension_now_supported():
    """Same field family via the split-by dimension slot — os_is_lts now
    resolves against the archived assets_history column (#1024)."""
    req = _req(select={"dimension": {"kind": "static", "name": "OS Is LTS"}})
    sql, _ = _builder().build_query(req)
    assert "os_is_lts" in sql
    assert "AS series" in sql


def test_history_trend_allows_common_field_filter():
    """Category IS archived into assets_history — the guard must not touch it."""
    req = _req(filters=[{
        "field_id": {"kind": "static", "name": "Category"},
        "operator": "equals",
        "value": "Workstation",
    }])
    sql, _ = _builder().build_query(req)
    assert "assets_history" in sql


def test_history_trend_tag_field_now_supported():
    """Tag fields resolve to the `tags` JSONB column, archived into
    assets_history as of migration 136 — a historical tag filter now builds
    valid SQL instead of a fail-loud 400 (#1024)."""
    req = _req(filters=[{
        "field_id": {"kind": "tag", "name": "Windows-Server"},
        "operator": "equals",
        "value": "true",
    }])
    sql, _ = _builder(tag_names=["Windows-Server"]).build_query(req)
    assert "tags->>'Windows-Server'" in sql
    assert "assets_history" in sql


def test_history_trend_posture_field_now_supported():
    """Posture fields resolve to the `posture_rules` JSONB column, archived as
    of migration 136 — a historical split by posture state now builds valid SQL
    instead of a fail-loud 400 (#1024). Exercised via the dimension slot."""
    req = _req(select={"dimension": {"kind": "posture", "category": "secured", "read": "state"}})
    sql, _ = _builder(posture_categories=["secured"]).build_query(req)
    assert "posture_rules->'secured'->>'state'" in sql
    assert "AS series" in sql


def test_history_trend_allows_extended_attribute_field():
    """core_enrichment fields resolve to the `extended_attributes` JSONB
    column, which IS archived into assets_history, so the guard must not
    reject them. Uses core_enrichment rather than extended_attribute because
    its catalog entries (CORE_ENRICHMENT_FIELDS) are static — no live-DB
    catalog rows needed to resolve — so this exercises the guard without
    fighting the mocked db_adapter."""
    req = _req(filters=[{
        "field_id": {"kind": "core_enrichment", "name": "IP Type"},
        "operator": "equals",
        "value": "public",
    }])
    sql, _ = _builder().build_query(req)
    assert "assets_history" in sql
