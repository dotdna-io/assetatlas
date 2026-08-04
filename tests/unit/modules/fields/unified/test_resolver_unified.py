# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.fields.types import FieldDataType
from src.modules.fields.catalog import FieldCatalog
from src.modules.fields.resolver import FieldResolver
from src.modules.fields.models import VirtualField


class _DB:
    def execute_query(self, *a, **k):
        return []


def _resolver(defs):
    cat = FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                       unified_fields_provider=lambda: defs)
    return FieldResolver(catalog=cat)


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
