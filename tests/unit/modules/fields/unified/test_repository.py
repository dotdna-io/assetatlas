# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
import json
from unittest.mock import MagicMock
from datetime import datetime, timezone
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
