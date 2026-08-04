# SPDX-License-Identifier: AGPL-3.0-or-later
# src/modules/fields/resolver.py
"""ResolvedField + FieldResolver.

Core invariant: user-submitted strings are used to *match* a catalog entry.
The matched catalog entry's strings, passed through _escape_literal, are what
appear in SQL — never user strings directly.
"""

from dataclasses import dataclass, field
from typing import FrozenSet, Optional, Tuple

from src.modules.fields.catalog import (
    CatalogEntry,
    FieldCatalog,
    VIRTUAL_FIELDS,
)
from src.modules.fields.models import (
    FieldId,
    InvalidFieldError,
    StaticField, CoreEnrichmentField, ExtendedAttributeField,
    IntegrationGroupField, TagField, PostureField, VirtualField,
)
from src.modules.fields.types import FieldDataType, FilterOperator


def _escape_literal(s: str) -> str:
    """Double single quotes for SQL literal safety. Used on all catalog strings."""
    return s.replace("'", "''")


@dataclass(frozen=True)
class ResolvedField:
    field_id: FieldId
    display_name: str
    data_type: FieldDataType
    operators: FrozenSet[FilterOperator]
    sql_kind: str = field(repr=False)   # "column" | "jsonb_path" | "jsonb_coalesce"
                                        # | "tag_boolean" | "posture_jsonb" | "virtual_expr"
    sql_expr: str = field(repr=False)

    def where_expr(self) -> str:
        return self.sql_expr

    def select_expr(self, alias: Optional[str] = None) -> str:
        if alias is None:
            return self.sql_expr
        # Double-quotes in SQL identifiers are escaped by doubling.
        # % is doubled because psycopg2 treats %-signs in the SQL string as
        # paramref markers when execute_query(sql, params) is called —
        # encode() emits %NN for non-ASCII / reserved characters, so an
        # alias like "ext:Entra%20Devices:attr" would otherwise trigger
        # `TypeError: dict is not a sequence` during param binding.
        alias_escaped = alias.replace('"', '""').replace('%', '%%')
        return f'{self.sql_expr} AS "{alias_escaped}"'

    def is_array(self) -> bool:
        return self.data_type in (FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY)

    def is_native_array(self) -> bool:
        """True iff this behaves like a PostgreSQL text[] value (supports
        `unnest(...)`): a real text[] column, or a unified `union` field whose
        generated `ARRAY(...)` expression supports the same idioms.

        JSONB-array fields (stored inside jsonb columns) return False here even
        though `is_array()` is True — they need different SQL idioms.
        """
        return self.is_array() and self.sql_kind in ("column", "virtual_array")

    def is_jsonb_sourced(self) -> bool:
        return self.sql_kind in ("jsonb_path", "jsonb_coalesce", "tag_boolean", "posture_jsonb")


class FieldResolver:
    """Stateless. Holds a reference to a FieldCatalog (which owns its own cache)."""

    def __init__(self, catalog: FieldCatalog):
        self._catalog = catalog

    def catalog(self) -> FieldCatalog:
        return self._catalog

    def resolve(self, field_id: FieldId) -> ResolvedField:
        entry = self._find_entry(field_id)
        if entry is None:
            raise InvalidFieldError(
                f"FieldId {field_id!r} does not resolve to any live catalog entry"
            )
        return self._build_resolved(field_id, entry)

    # ---------- catalog lookup ----------

    def _find_entry(self, field_id: FieldId) -> Optional[CatalogEntry]:
        index = self._catalog.index()
        if isinstance(field_id, StaticField):
            return index.get(("static", field_id.name))
        if isinstance(field_id, CoreEnrichmentField):
            return index.get(("core_enrichment", field_id.name))
        if isinstance(field_id, ExtendedAttributeField):
            return index.get(("extended_attribute", field_id.source, field_id.attribute))
        if isinstance(field_id, IntegrationGroupField):
            return index.get(("integration_group", field_id.group, field_id.attribute))
        if isinstance(field_id, TagField):
            return index.get(("tag", field_id.name))
        if isinstance(field_id, PostureField):
            return index.get(("posture", field_id.category, field_id.read))
        if isinstance(field_id, VirtualField):
            return index.get(("virtual", field_id.name))
        return None

    # ---------- SQL emission ----------

    def _build_resolved(self, field_id: FieldId, entry: CatalogEntry) -> ResolvedField:
        sql_kind, sql_expr = self._emit_sql(entry)
        return ResolvedField(
            field_id=field_id,
            display_name=entry.display_name,
            data_type=entry.data_type,
            operators=frozenset(entry.operators),
            sql_kind=sql_kind,
            sql_expr=sql_expr,
        )

    def _emit_sql(self, entry: CatalogEntry) -> Tuple[str, str]:
        if entry.kind_name == "static":
            return "column", entry.static_db_column  # catalog column names are trusted
        if entry.kind_name == "core_enrichment":
            source, attr = entry.core_path.split(".", 1)
            src_esc = _escape_literal(source)
            attr_esc = _escape_literal(attr)
            arrow = "->" if entry.is_array else "->>"
            return "jsonb_path", f"extended_attributes->'{src_esc}'{arrow}'{attr_esc}'"
        if entry.kind_name == "extended_attribute":
            src_esc = _escape_literal(entry.source)
            attr_esc = _escape_literal(entry.attribute)
            return "jsonb_path", f"extended_attributes->'{src_esc}'->>'{attr_esc}'"
        if entry.kind_name == "integration_group":
            attr_esc = _escape_literal(entry.attribute)
            paths = [
                f"extended_attributes->'{_escape_literal(m)}'->>'{attr_esc}'"
                for m in entry.member_sources  # already sorted in catalog
            ]
            return "jsonb_coalesce", f"COALESCE({', '.join(paths)})"
        if entry.kind_name == "tag":
            tag_esc = _escape_literal(entry.tag_name)
            return "tag_boolean", f"tags->>'{tag_esc}'"
        if entry.kind_name == "posture":
            cat_esc = _escape_literal(entry.posture_category)
            read_esc = _escape_literal(entry.posture_read)
            return "posture_jsonb", f"posture_rules->'{cat_esc}'->>'{read_esc}'"
        if entry.kind_name == "virtual":
            if entry.unified_function is not None:
                # User-defined unified field: generate merge SQL by composing each
                # source's already-resolved, escaped SQL. Lazy import avoids any
                # import cycle through the fields package.
                from src.modules.fields.unified.merge_sql import build_merge_sql
                sql = build_merge_sql(self, entry.unified_function, entry.unified_sources)
                kind = "virtual_array" if entry.unified_function == "union" else "virtual_expr"
                return kind, sql
            # Built-in virtual: VIRTUAL_FIELDS[name]["sql"] is trusted — hardcoded.
            return "virtual_expr", VIRTUAL_FIELDS[entry.virtual_name]["sql"]
        raise InvalidFieldError(f"unknown catalog entry kind: {entry.kind_name}")
