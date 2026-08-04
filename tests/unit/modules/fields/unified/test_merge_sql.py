# SPDX-License-Identifier: AGPL-3.0-or-later
from dataclasses import dataclass
from src.modules.fields.types import FieldDataType as DT


@dataclass
class StubField:
    expr: str
    data_type: DT
    sql_kind: str  # "column" | "jsonb_path" | "virtual_array" | ...

    def where_expr(self):
        return self.expr

    def is_array(self):
        return self.data_type in (DT.STRING_ARRAY, DT.JSONB_ARRAY)

    def is_native_array(self):
        return self.is_array() and self.sql_kind in ("column", "virtual_array")


class _Resolver:
    def __init__(self, by_key):
        self._by_key = by_key

    def resolve(self, field_id):
        from src.modules.fields.encoding import encode as _enc
        from src.modules.fields.models import InvalidFieldError
        key = _enc(field_id)
        if key not in self._by_key:
            raise InvalidFieldError(key)
        return self._by_key[key]


from src.modules.fields.unified.merge_sql import build_merge_sql


def _r(mapping):
    return _Resolver(mapping)


def test_coalesce_text_jsonb_sources():
    k1, k2 = "ext:EntraA:lentity", "ext:EntraB:lentity"
    r = _r({
        k1: StubField("extended_attributes->'EntraA'->>'lentity'", DT.STRING, "jsonb_path"),
        k2: StubField("extended_attributes->'EntraB'->>'lentity'", DT.STRING, "jsonb_path"),
    })
    sql = build_merge_sql(r, "coalesce", [k1, k2])
    assert sql == (
        "COALESCE(NULLIF(extended_attributes->'EntraA'->>'lentity', ''), "
        "NULLIF(extended_attributes->'EntraB'->>'lentity', ''))"
    )


def test_max_jsonb_date_uses_try_cast():
    k1, k2 = "ext:A:seen", "ext:B:seen"
    r = _r({
        k1: StubField("extended_attributes->'A'->>'seen'", DT.TIMESTAMP, "jsonb_path"),
        k2: StubField("extended_attributes->'B'->>'seen'", DT.TIMESTAMP, "jsonb_path"),
    })
    sql = build_merge_sql(r, "max", [k1, k2])
    assert sql == (
        "GREATEST(aa_try_ts(NULLIF(extended_attributes->'A'->>'seen', '')), "
        "aa_try_ts(NULLIF(extended_attributes->'B'->>'seen', '')))"
    )


def test_min_native_date_no_cast():
    k1, k2 = "static:First Seen", "static:Last Seen"
    r = _r({
        "static:First%20Seen": StubField("first_time", DT.TIMESTAMP, "column"),
        "static:Last%20Seen": StubField("last_time", DT.TIMESTAMP, "column"),
    })
    assert build_merge_sql(r, "min", [k1, k2]) == "LEAST(first_time, last_time)"


def test_union_scalar_plus_native_array():
    k1, k2 = "ext:A:owner", "static:Owner"
    r = _r({
        k1: StubField("extended_attributes->'A'->>'owner'", DT.STRING, "jsonb_path"),
        k2: StubField("owner", DT.STRING_ARRAY, "column"),
    })
    sql = build_merge_sql(r, "union", [k1, k2])
    assert sql == (
        "ARRAY(SELECT DISTINCT v FROM unnest("
        "(CASE WHEN (extended_attributes->'A'->>'owner')::text IS NULL "
        "OR (extended_attributes->'A'->>'owner')::text = '' THEN '{}'::text[] "
        "ELSE ARRAY[(extended_attributes->'A'->>'owner')::text] END) "
        "|| COALESCE(owner, '{}'::text[])"
        ") AS v WHERE v IS NOT NULL AND v <> '')"
    )


def test_all_sources_missing_returns_null():
    r = _r({})  # resolve() will raise InvalidFieldError -> treated as missing
    assert build_merge_sql(r, "coalesce", ["ext:Gone:x", "ext:Gone:y"]) == "NULL"
    assert build_merge_sql(r, "union", ["ext:Gone:x", "ext:Gone:y"]) == "NULL::text[]"
