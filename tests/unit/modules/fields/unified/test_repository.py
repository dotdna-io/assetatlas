# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
import json
import logging
from unittest.mock import MagicMock
from datetime import datetime, timezone

import psycopg2.errors

from src.modules.fields.types import FieldDataType
from src.modules.fields.unified.repository import UnifiedFieldsRepository

_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "slug": "lentity",
    "name": "lentity",
    "function": "coalesce",
    "sources": ["ext:A:lentity", "ext:B:lentity"],
    "data_type": "string",
    "created_at": datetime(2026, 6, 22, tzinfo=timezone.utc),
    "updated_at": datetime(2026, 6, 22, tzinfo=timezone.utc),
}


def test_create_serializes_sources_as_json_and_returns_row():
    db = MagicMock()
    db.execute_query.return_value = [dict(_ROW)]
    repo = UnifiedFieldsRepository(db)
    out = repo.create(slug="lentity", name="lentity", function="coalesce",
                      sources=["ext:A:lentity", "ext:B:lentity"], data_type="string")
    sql, params = db.execute_query.call_args[0]
    assert "INSERT INTO unified_fields" in sql
    assert params["sources"] == json.dumps(["ext:A:lentity", "ext:B:lentity"])
    assert params["data_type"] == "string"
    assert out["id"] == _ROW["id"]
    assert out["sources"] == ["ext:A:lentity", "ext:B:lentity"]


def test_list_for_catalog_maps_data_type_to_enum():
    db = MagicMock()
    db.execute_query.return_value = [{
        "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["ext:A:lentity", "ext:B:lentity"], "data_type": "string",
    }]
    repo = UnifiedFieldsRepository(db)
    out = repo.list_for_catalog()
    assert out[0]["data_type"] == FieldDataType.STRING
    assert out[0]["sources"] == ["ext:A:lentity", "ext:B:lentity"]


def test_name_exists_excludes_self():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = UnifiedFieldsRepository(db)
    assert repo.name_exists("lentity", exclude_id="abc") is False
    sql, params = db.execute_query.call_args[0]
    assert "id <> %(id)s::uuid" in sql
    assert params["id"] == "abc"


def test_delete_returns_true_when_row_removed():
    db = MagicMock()
    db.execute_query.return_value = [{"id": _ROW["id"]}]
    repo = UnifiedFieldsRepository(db)
    assert repo.delete(_ROW["id"]) is True


# ── F6: list_for_catalog skips rows with unknown data_type ─────────────────────
def test_list_for_catalog_skips_unknown_data_type():
    """Rows with an unrecognised data_type must be silently skipped (F6 fix)."""
    db = MagicMock()
    db.execute_query.return_value = [
        {"slug": "good", "name": "Good", "function": "coalesce",
         "sources": ["ext:A:x"], "data_type": "string"},
        {"slug": "bad", "name": "Bad", "function": "coalesce",
         "sources": ["ext:A:y"], "data_type": "bogus"},
    ]
    repo = UnifiedFieldsRepository(db)
    out = repo.list_for_catalog()
    assert len(out) == 1
    assert out[0]["slug"] == "good"


# ── F7: create maps IntegrityError to ConflictError ───────────────────────────
def test_create_maps_integrity_error_to_conflict():
    """DB IntegrityError on INSERT must surface as ConflictError (F7 fix)."""
    import psycopg2
    from src.api.middleware.error_handler import ConflictError
    db = MagicMock()
    db.execute_query.side_effect = psycopg2.IntegrityError("duplicate key")
    repo = UnifiedFieldsRepository(db)
    with pytest.raises(ConflictError):
        repo.create(slug="dup", name="Dup", function="coalesce",
                    sources=["ext:A:x"], data_type="string")


# ── materialized_at (#1405) ───────────────────────────────────────────────────
def test_list_for_catalog_selects_and_carries_materialized_at():
    db = MagicMock()
    db.execute_query.return_value = [{
        "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["ext:A:lentity", "ext:B:lentity"], "data_type": "string",
        "materialized_at": datetime(2026, 9, 9, tzinfo=timezone.utc),
    }]
    repo = UnifiedFieldsRepository(db)
    out = repo.list_for_catalog()
    sql = db.execute_query.call_args[0][0]
    assert "materialized_at" in sql
    assert out[0]["materialized_at"] == datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_row_maps_materialized_at():
    db = MagicMock()
    row = dict(_ROW, materialized_at=datetime(2026, 9, 9, tzinfo=timezone.utc))
    db.execute_query.return_value = [row]
    repo = UnifiedFieldsRepository(db)
    out = repo.get(_ROW["id"])
    assert out["materialized_at"] == datetime(2026, 9, 9, tzinfo=timezone.utc)
    assert "materialized_at" in db.execute_query.call_args[0][0]


def test_set_materialized_at_stamps_now():
    db = MagicMock()
    db.execute_query.return_value = [{"slug": "a"}, {"slug": "b"}]
    repo = UnifiedFieldsRepository(db)
    n = repo.set_materialized_at(["a", "b"], True)
    sql, params = db.execute_query.call_args[0]
    assert "UPDATE unified_fields SET materialized_at = NOW()" in sql
    assert "WHERE slug = ANY(%(slugs)s)" in sql
    assert params == {"slugs": ["a", "b"]}
    assert n == 2


def test_set_materialized_at_clears_to_null():
    db = MagicMock()
    db.execute_query.return_value = [{"slug": "a"}]
    repo = UnifiedFieldsRepository(db)
    n = repo.set_materialized_at(["a"], False)
    sql, params = db.execute_query.call_args[0]
    assert "UPDATE unified_fields SET materialized_at = NULL" in sql
    assert "NOW()" not in sql
    assert params == {"slugs": ["a"]}
    assert n == 1


def test_set_materialized_at_with_no_slugs_issues_no_sql():
    db = MagicMock()
    repo = UnifiedFieldsRepository(db)
    assert repo.set_materialized_at([], True) == 0
    db.execute_query.assert_not_called()


# ── Pre-migration-149 tolerance ───────────────────────────────────────────────
# On a database without migration 149 the 6-column SELECT raises UndefinedColumn
# (and, inside a request scope, poisons the request's whole transaction). The
# repository must probe first and degrade: 5-column SELECT, materialized_at
# None, one WARNING per process — never an error.

class _Pre149Db:
    """The pre-149 database: the probe finds no column, and any SQL naming
    materialized_at raises UndefinedColumn — it must never be issued."""
    def __init__(self, rows):
        self.rows = rows
        self.probes = 0
        self.queries = []

    def execute_query(self, sql, params=None):
        if "information_schema.columns" in sql:
            self.probes += 1
            return []
        if "materialized_at" in sql:
            raise psycopg2.errors.UndefinedColumn(
                'column "materialized_at" does not exist')
        self.queries.append((sql, params))
        return list(self.rows)


class _Post149Db:
    """Probe finds the column; records everything past it."""
    def __init__(self, rows):
        self.rows = rows
        self.probes = 0
        self.queries = []

    def execute_query(self, sql, params=None):
        if "information_schema.columns" in sql:
            self.probes += 1
            return [{"?column?": 1}]
        self.queries.append((sql, params))
        return list(self.rows)


_CATALOG_ROW = {
    "slug": "pula", "name": "pula", "function": "coalesce",
    "sources": ["ext:A:pula", "ext:B:pula"], "data_type": "string",
}


def test_list_for_catalog_without_the_column_selects_five_and_returns_none():
    db = _Pre149Db([dict(_CATALOG_ROW)])
    out = UnifiedFieldsRepository(db).list_for_catalog()
    sql = db.queries[0][0]
    assert "materialized_at" not in sql
    assert "SELECT slug, name, function, sources, data_type" in sql
    assert "FROM unified_fields ORDER BY lower(name)" in sql
    assert out[0]["slug"] == "pula"
    assert out[0]["materialized_at"] is None


def test_list_for_catalog_with_the_column_selects_six_and_carries_the_stamp():
    stamp = datetime(2026, 9, 9, tzinfo=timezone.utc)
    db = _Post149Db([dict(_CATALOG_ROW, materialized_at=stamp)])
    out = UnifiedFieldsRepository(db).list_for_catalog()
    sql = db.queries[0][0]
    assert "data_type, materialized_at" in sql
    assert out[0]["materialized_at"] == stamp


def test_list_for_catalog_probes_the_column_once_when_present():
    db = _Post149Db([dict(_CATALOG_ROW)])
    repo = UnifiedFieldsRepository(db)
    repo.list_for_catalog()
    repo.list_for_catalog()
    assert db.probes == 1   # True cached for the process


def test_list_for_catalog_reprobes_while_the_column_is_absent():
    db = _Pre149Db([dict(_CATALOG_ROW)])
    repo = UnifiedFieldsRepository(db)
    repo.list_for_catalog()
    repo.list_for_catalog()
    assert db.probes == 2   # False deliberately not cached


def test_set_materialized_at_without_the_column_is_a_noop_returning_zero():
    db = _Pre149Db([])
    repo = UnifiedFieldsRepository(db)
    assert repo.set_materialized_at(["pula"], True) == 0
    assert repo.set_materialized_at(["pula"], False) == 0
    assert db.queries == []   # no UPDATE issued, probes only


def test_set_materialized_at_with_the_column_updates_as_before():
    db = _Post149Db([{"slug": "pula"}])
    repo = UnifiedFieldsRepository(db)
    assert repo.set_materialized_at(["pula"], True) == 1
    sql, params = db.queries[0]
    assert "UPDATE unified_fields SET materialized_at = NOW()" in sql
    assert params == {"slugs": ["pula"]}


def test_the_pre149_warning_is_once_per_process_not_per_call(caplog):
    db = _Pre149Db([dict(_CATALOG_ROW)])
    repo = UnifiedFieldsRepository(db)
    with caplog.at_level(logging.WARNING):
        repo.list_for_catalog()
        repo.list_for_catalog()
        repo.set_materialized_at(["pula"], True)
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "migration 149" in r.message]
    assert len(warnings) == 1
