# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the aggregate-function + flat-logic threading added to
AssetRepository for the chatbot ``query_assets`` primitive (plan §3.3).

WHY these matter:
- ``aggregate_assets`` was COUNT(*)-only. A user asking "total disk size by
  category" (SUM) or "average confidence per source" (AVG) would silently get a
  *row count* instead — the wrong question answered with a plausible number.
  These tests pin that the requested aggregate function actually reaches the
  SQL, and that COUNT stays the default (back-compat for every existing caller).
- ``logic='or'`` must OR the filter clauses. Joining with AND when the user
  asked for OR returns a *narrower* set than requested — a correctness bug, and
  the inverse of the §4 "never silently widen" risk. Default stays AND so the
  Explorer / asset_service / MCP callers are byte-for-byte unchanged.
"""

from unittest.mock import MagicMock

import pytest

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.encoding import encode
from src.modules.fields.models import StaticField


def _make_repo():
    """Repo over a catalog with the built-in static/virtual fields (no DB hits
    for static resolution) and a mocked adapter we can inspect for SQL."""
    db = MagicMock()
    db.execute_query.return_value = []
    resolver = FieldResolver(catalog=FieldCatalog(db_adapter=db))
    return AssetRepository(db, resolver=resolver), db


def _static(name):
    return encode(StaticField(kind="static", name=name))


def _static_filter(name, op, value):
    return {"field_id": {"kind": "static", "name": name}, "op": op, "value": value}


def _agg_sql(db):
    """The single SELECT aggregate_assets issued (the GROUP BY query)."""
    assert db.execute_query.call_args is not None, "no query was executed"
    return db.execute_query.call_args[0][0]


# ===========================================================================
# Aggregate functions threaded into the SELECT (the only new SQL, §3.3)
# ===========================================================================

class TestAggregateFunctions:
    def test_count_is_the_default_and_uses_count_star(self):
        """No ``agg`` arg ⇒ COUNT(*), exactly as before. Guards every existing
        caller (asset_service, /api/assets/aggregate, the MCP shim)."""
        repo, db = _make_repo()
        db.execute_query.return_value = [{"row_val": "server", "count": 7}]
        result = repo.aggregate_assets(row_field=_static("Category"))
        assert "COUNT(*)" in _agg_sql(db)
        assert result["data"]["server"]["count"] == 7
        assert result["grand_total"] == 7

    def test_sum_aggregates_the_metric_not_the_rowcount(self):
        """SUM must emit SUM(<metric>), never COUNT(*) — otherwise "total X by
        category" silently answers "how many rows by category"."""
        repo, db = _make_repo()
        db.execute_query.return_value = [{"row_val": "server", "count": 42}]
        repo.aggregate_assets(
            row_field=_static("Category"),
            agg="SUM", metric_field=_static("Confidence Score"),
        )
        sql = _agg_sql(db)
        assert "SUM(" in sql
        assert "COUNT(*)" not in sql

    def test_avg_emits_avg_of_metric(self):
        repo, db = _make_repo()
        db.execute_query.return_value = [{"row_val": "server", "count": 0.8}]
        repo.aggregate_assets(
            row_field=_static("Category"),
            agg="AVG", metric_field=_static("Confidence Score"),
        )
        assert "AVG(" in _agg_sql(db)

    def test_min_max_emit_their_functions(self):
        for agg in ("MIN", "MAX"):
            repo, db = _make_repo()
            db.execute_query.return_value = [{"row_val": "server", "count": 1}]
            repo.aggregate_assets(
                row_field=_static("Category"),
                agg=agg, metric_field=_static("Confidence Score"),
            )
            assert f"{agg}(" in _agg_sql(db)

    def test_count_distinct_emits_count_distinct(self):
        repo, db = _make_repo()
        db.execute_query.return_value = [{"row_val": "server", "count": 3}]
        repo.aggregate_assets(
            row_field=_static("Category"),
            agg="COUNT_DISTINCT", metric_field=_static("Owner"),
        )
        assert "COUNT(DISTINCT" in _agg_sql(db)

    def test_numeric_agg_without_metric_field_raises(self):
        """SUM/AVG/MIN/MAX have nothing to aggregate without a metric_field;
        a loud ValueError beats silently degrading to a row count."""
        repo, _ = _make_repo()
        with pytest.raises(ValueError):
            repo.aggregate_assets(row_field=_static("Category"), agg="SUM")

    def test_sum_over_array_field_raises(self):
        """SUM(text[]) is nonsense; reject it loudly rather than emit invalid
        SQL the DB rejects with an opaque error."""
        repo, _ = _make_repo()
        with pytest.raises(ValueError):
            repo.aggregate_assets(
                row_field=_static("Category"),
                agg="SUM", metric_field=_static("Data Source"),
            )

    def test_unknown_agg_raises(self):
        repo, _ = _make_repo()
        with pytest.raises(ValueError):
            repo.aggregate_assets(row_field=_static("Category"), agg="MEDIAN")

    def test_two_dim_count_unchanged(self):
        """2-D COUNT pivot keeps its shape (back-compat)."""
        repo, db = _make_repo()
        db.execute_query.return_value = [
            {"row_val": "server", "col_val": "Windows", "count": 4},
            {"row_val": "server", "col_val": "Linux", "count": 2},
        ]
        result = repo.aggregate_assets(
            row_field=_static("Category"), col_field=_static("OS Normalized"),
        )
        assert "COUNT(*)" in _agg_sql(db)
        assert result["data"]["server"]["Windows"] == 4
        assert result["grand_total"] == 6


# ===========================================================================
# Flat AND/OR filter logic (§8 ratified) — default stays AND
# ===========================================================================

class TestFilterLogic:
    def _two_filters(self):
        return [
            _static_filter("Category", "eq", "server"),
            _static_filter("Category", "eq", "workstation"),
        ]

    def test_list_assets_default_logic_is_and(self):
        repo, db = _make_repo()
        repo.list_assets(filters=self._two_filters())
        # Both calls (count + data) AND the two clauses together.
        joined = " ".join(c[0][0] for c in db.execute_query.call_args_list)
        assert " OR " not in joined

    def test_list_assets_logic_or_joins_with_or(self):
        repo, db = _make_repo()
        repo.list_assets(filters=self._two_filters(), logic="or")
        joined = " ".join(c[0][0] for c in db.execute_query.call_args_list)
        assert " OR " in joined

    def test_aggregate_assets_logic_or(self):
        repo, db = _make_repo()
        db.execute_query.return_value = []
        repo.aggregate_assets(
            row_field=_static("Category"), filters=self._two_filters(),
            logic="or",
        )
        assert " OR " in _agg_sql(db)

    def test_get_field_values_logic_or(self):
        repo, db = _make_repo()
        db.execute_query.return_value = []
        repo.get_field_values(
            field=_static("Owner"), filters=self._two_filters(), logic="or",
        )
        joined = " ".join(c[0][0] for c in db.execute_query.call_args_list)
        assert " OR " in joined
