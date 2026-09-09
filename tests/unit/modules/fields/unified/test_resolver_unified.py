# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone

import pytest

from src.modules.fields.types import FieldDataType
from src.modules.fields.catalog import FieldCatalog
from src.modules.fields.resolver import FieldResolver
from src.modules.fields.models import VirtualField

_STAMPED = datetime(2026, 9, 9, tzinfo=timezone.utc)


class _DB:
    def execute_query(self, *a, **k):
        return []


def _resolver(defs):
    cat = FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                       unified_fields_provider=lambda: defs)
    return FieldResolver(catalog=cat)


def _forbid_live_sql(monkeypatch):
    """A stamped field must not consult build_merge_sql at all."""
    def _boom(*a, **k):
        raise AssertionError("build_merge_sql called for a materialized field")
    monkeypatch.setattr(
        "src.modules.fields.unified.merge_sql.build_merge_sql", _boom,
    )


def test_resolve_coalesce_unified_field_is_scalar_virtual_expr(monkeypatch):
    # Stub build_merge_sql so this test is independent of the generator internals.
    monkeypatch.setattr(
        "src.modules.fields.unified.merge_sql.build_merge_sql",
        lambda r, fn, src: "COALESCE(a, b)",
    )
    r = _resolver([{
        "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["static:Hostname", "static:FQDN"], "data_type": FieldDataType.STRING,
    }])
    rf = r.resolve(VirtualField(kind="virtual", name="lentity"))
    assert rf.sql_kind == "virtual_expr"
    assert rf.sql_expr == "COALESCE(a, b)"
    assert rf.is_native_array() is False


def test_resolve_union_unified_field_is_native_array(monkeypatch):
    monkeypatch.setattr(
        "src.modules.fields.unified.merge_sql.build_merge_sql",
        lambda r, fn, src: "ARRAY(SELECT 1)",
    )
    r = _resolver([{
        "slug": "all_names", "name": "all_names", "function": "union",
        "sources": ["static:Hostname", "static:FQDN"], "data_type": FieldDataType.STRING_ARRAY,
    }])
    rf = r.resolve(VirtualField(kind="virtual", name="all_names"))
    assert rf.sql_kind == "virtual_array"
    assert rf.is_array() is True
    assert rf.is_native_array() is True


# ── materialized (stamped) fields: read the stored value ─────────────────────

@pytest.mark.parametrize("definition, expected", [
    ({"slug": "lentity", "name": "lentity", "function": "coalesce",
      "sources": ["static:Hostname", "static:FQDN"], "data_type": FieldDataType.STRING},
     "unified_values->>'lentity'"),
    ({"slug": "fleet_size", "name": "fleet_size", "function": "max",
      "sources": ["static:Confidence Score", "static:Confidence Score"],
      "data_type": FieldDataType.FLOAT},
     "(unified_values->>'fleet_size')::numeric"),
    ({"slug": "seen", "name": "seen", "function": "coalesce",
      "sources": ["static:First Seen", "static:Last Seen"],
      "data_type": FieldDataType.TIMESTAMP},
     "(unified_values->>'seen')::timestamptz"),
], ids=["text", "number", "date"])
def test_stamped_scalar_field_reads_typed_reference(monkeypatch, definition, expected):
    _forbid_live_sql(monkeypatch)
    r = _resolver([{**definition, "materialized_at": _STAMPED}])
    rf = r.resolve(VirtualField(kind="virtual", name=definition["slug"]))
    assert rf.sql_kind == "virtual_expr"
    assert rf.sql_expr == expected
    assert rf.data_type == definition["data_type"]
    assert rf.is_native_array() is False
    assert rf.is_jsonb_sourced() is False


def test_stamped_union_field_reads_array_reference(monkeypatch):
    _forbid_live_sql(monkeypatch)
    r = _resolver([{
        "slug": "all_names", "name": "all_names", "function": "union",
        "sources": ["static:Hostname", "static:FQDN"],
        "data_type": FieldDataType.STRING_ARRAY, "materialized_at": _STAMPED,
    }])
    rf = r.resolve(VirtualField(kind="virtual", name="all_names"))
    assert rf.sql_kind == "virtual_array"
    assert rf.sql_expr == (
        "COALESCE(ARRAY(SELECT jsonb_array_elements_text("
        "CASE WHEN jsonb_typeof(unified_values->'all_names')='array' "
        "THEN unified_values->'all_names' END)), '{}'::text[])"
    )
    assert rf.is_array() is True
    assert rf.is_native_array() is True
    assert rf.is_jsonb_sourced() is False


def test_unstamped_field_keeps_live_merge_sql(monkeypatch):
    # materialized_at NULL (or absent) → exactly today's live output.
    monkeypatch.setattr(
        "src.modules.fields.unified.merge_sql.build_merge_sql",
        lambda r, fn, src: "COALESCE(a, b)",
    )
    r = _resolver([{
        "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["static:Hostname", "static:FQDN"], "data_type": FieldDataType.STRING,
        "materialized_at": None,
    }])
    rf = r.resolve(VirtualField(kind="virtual", name="lentity"))
    assert rf.sql_kind == "virtual_expr"
    assert rf.sql_expr == "COALESCE(a, b)"


def test_stamped_field_slug_is_escaped(monkeypatch):
    # Slugs come from the DB; a quote must be doubled like every other literal.
    _forbid_live_sql(monkeypatch)
    r = _resolver([{
        "slug": "we'ird", "name": "we'ird", "function": "coalesce",
        "sources": ["static:Hostname", "static:FQDN"], "data_type": FieldDataType.STRING,
        "materialized_at": _STAMPED,
    }])
    rf = r.resolve(VirtualField(kind="virtual", name="we'ird"))
    assert rf.sql_expr == "unified_values->>'we''ird'"
