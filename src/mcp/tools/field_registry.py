# SPDX-License-Identifier: AGPL-3.0-or-later
"""Field registry tools for the AssetAtlas MCP server.

Tools: get_field_registry
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db
from src.modules.fields.catalog import _entry_field_id, build_catalog_response
from src.modules.fields.encoding import encode
from src.modules.fields.operators import OPERATOR_REGISTRY

# Cap list-view size so a customer with hundreds of fields doesn't blow LLM
# context. Callers can request a single row by passing `field_name`.
_LIST_LIMIT = 200
_DESC_TRUNC_CHARS = 60

# describe_catalog bucket vocabulary (plan §2.A). A field matches a bucket when
# the bucket equals EITHER its response `category` (which splits static into
# target/advanced) OR its `field_id.kind` (which distinguishes
# extended_attribute vs integration_group, core_enrichment, etc.) — the plan's
# bucket names span both layers.
_CATALOG_BUCKETS = (
    "target", "advanced", "core_enrichment", "extended_attribute",
    "integration_group", "tag", "posture", "virtual",
)
_CATALOG_LIST_DEFAULT = 50
_CATALOG_LIST_MAX = 200


def register_tools(mcp):
    """Register field-registry tools on the FastMCP server."""

    @mcp.tool()
    def get_field_registry(
        field_name: str | None = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List asset field definitions, or return details for one field."""
        return get_field_registry_impl(get_db(ctx), field_name=field_name)


def get_field_registry_impl(db, *, field_name: str | None = None) -> str:
    if field_name is not None:
        return _detail_form(db, field_name)
    return _list_form(db)


def _list_form(db) -> str:
    rows: List[Dict[str, Any]] = db.execute_query(
        """
        SELECT field_name, display_name, description, field_type,
               data_source, searchable
        FROM field_registry
        ORDER BY field_type, field_name
        LIMIT %(lim)s
        """,
        {"lim": _LIST_LIMIT},
    ) or []

    if not rows:
        return "Field registry is empty."

    # Group by field_type preserving SQL order (already sorted by field_type).
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r["field_type"], []).append(r)

    sections: List[str] = ["# Field registry"]
    for ftype, group in grouped.items():
        sections.append("")
        sections.append(f"## {ftype} ({len(group)})")
        sections.append(
            "| Field name | Display name | Data source | Searchable | Description |"
        )
        sections.append("| --- | --- | --- | --- | --- |")
        for r in group:
            desc = r.get("description") or ""
            desc_short = desc[:_DESC_TRUNC_CHARS]
            searchable = "✓" if r.get("searchable") else "✗"
            sections.append(
                f"| {r['field_name']} | {r.get('display_name') or ''} | "
                f"{r.get('data_source') or '—'} | {searchable} | "
                f"{desc_short} |"
            )

    return "\n".join(sections)


def _detail_form(db, field_name: str) -> str:
    rows = db.execute_query(
        """
        SELECT field_name, display_name, description, field_type, db_path,
               data_source, description_source, searchable, created_at, updated_at
        FROM field_registry
        WHERE field_name = %(fn)s
        """,
        {"fn": field_name},
    )
    field: Optional[Dict[str, Any]] = rows[0] if rows else None
    if not field:
        return f"Field {field_name!r} not found in registry."

    searchable = "✓" if field.get("searchable") else "✗"
    data_source = field.get("data_source") or "—"
    description_source = field.get("description_source") or "—"
    description = field.get("description") or "_no description_"

    lines = [
        f"# {field.get('display_name') or field['field_name']} ({field['field_name']})",
        f"- type: {field.get('field_type')}",
        f"- db path: {field.get('db_path')}",
        f"- data source: {data_source}",
        f"- searchable: {searchable}",
        f"- description source: {description_source}",
        f"- created: {field.get('created_at')}",
        f"- updated: {field.get('updated_at')}",
        "",
        f"**Description:** {description}",
    ]
    return "\n".join(lines)


def list_catalog_fields_impl(resolver, *, search=None, limit=50) -> str:
    """List queryable fields (display name -> encoded key) from the live
    catalog so the model can discover data-source-specific fields. Results
    are capped and search-filtered to bound LLM context."""
    limit = max(1, min(limit, _LIST_LIMIT))
    needle = (search or "").strip().lower()
    matches = [
        e for e in resolver.catalog().entries()
        if not needle or needle in e.display_name.lower()
    ]
    if not matches:
        return ("No matching fields found. Try a broader search term, or omit "
                "`search` to list the first fields.")
    shown = matches[:limit]
    lines = [f"Fields ({len(matches)} match, showing {len(shown)}):",
             "| Field | encoded_key | type |", "| --- | --- | --- |"]
    for e in shown:
        # Encode only the rows we actually show — avoids building FieldIds for
        # the truncated tail on large catalogs.
        lines.append(
            f"| {e.display_name} | {encode(_entry_field_id(e))} | "
            f"{e.data_type.value} |"
        )
    if len(matches) > len(shown):
        lines.append(
            f"...{len(matches) - len(shown)} more — narrow with `search`."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# describe_catalog — the discovery backbone for query_assets / run_chart
# ---------------------------------------------------------------------------

def describe_catalog_impl(
    resolver, *, view: str = "fields", bucket: Optional[str] = None,
    source: Optional[str] = None, search: Optional[str] = None,
    offset: int = 0, limit: int = _CATALOG_LIST_DEFAULT,
) -> str:
    """The model's field/operator discovery tool.

    ``view='fields'`` lists catalog fields WITH their per-field operator set
    (``operator_specs``) from ``build_catalog_response`` — the key gap the old
    ``list_fields`` left, since the model otherwise can't tell which operators a
    field accepts. ``view='operators'`` dumps the operator registry with aliases.
    Folds the old ``list_fields`` / ``list_catalog_fields`` / (value-side)
    ``get_asset_categories`` discovery into one tool."""
    if view == "operators":
        return _operators_view()
    if view != "fields":
        return (f"Unknown view '{view}'. Use view='fields' (default) or "
                "view='operators'.")
    return _fields_view(resolver, bucket=bucket, source=source, search=search,
                        offset=offset, limit=limit)


def _entry_matches(entry, *, bucket, needle, src) -> bool:
    if bucket is not None and bucket not in (
        entry.get("category"), (entry.get("field_id") or {}).get("kind"),
    ):
        return False
    if needle and needle not in entry["display_name"].lower():
        return False
    if src:
        # Exact match on the field's own source: extended_attribute fields carry
        # it in field_id.source; integration_group fields list their sources in
        # member_sources. (Matching a display-name "<src> - " prefix would
        # false-positive when a source name itself contains " - ".)
        fid = entry.get("field_id") or {}
        members = [m.lower() for m in (entry.get("member_sources") or [])]
        if src != (fid.get("source") or "").lower() and src not in members:
            return False
    return True


def _fields_view(resolver, *, bucket, source, search, offset, limit) -> str:
    if bucket is not None and bucket not in _CATALOG_BUCKETS:
        # Loud, not a silent fall-through to the whole catalog (§4).
        return (f"Unknown bucket '{bucket}'. Valid buckets: "
                f"{', '.join(_CATALOG_BUCKETS)}.")

    entries = build_catalog_response(resolver.catalog()).get("entries", [])
    needle = (search or "").strip().lower()
    src = (source or "").strip().lower()
    matches = [
        e for e in entries
        if _entry_matches(e, bucket=bucket, needle=needle, src=src)
    ]

    if not matches:
        scope = []
        if bucket:
            scope.append(f"bucket '{bucket}'")
        if source:
            scope.append(f"source '{source}'")
        if search:
            scope.append(f"search '{search}'")
        scope_s = (" for " + ", ".join(scope)) if scope else ""
        return (f"No matching fields{scope_s}. They may not exist on this "
                "deployment — broaden or drop the filters, or call "
                "describe_catalog with view='fields' and no filters.")

    total = len(matches)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or _CATALOG_LIST_DEFAULT), _CATALOG_LIST_MAX))
    if offset >= total:
        # Loud + recoverable, not an empty table with a backwards 'showing
        # N+1-N' header and no hint (§4).
        return (f"Offset {offset} is past the end — only {total} field(s) "
                f"match. Use `offset` 0..{total - 1}, or drop it.")
    page = matches[offset:offset + limit]
    shown_end = offset + len(page)

    scope_bits = []
    if bucket:
        scope_bits.append(f"bucket={bucket}")
    if source:
        scope_bits.append(f"source={source}")
    if search:
        scope_bits.append(f"search={search!r}")
    scope_note = (" · " + ", ".join(scope_bits)) if scope_bits else ""

    lines = [
        f"Catalog fields ({total} total{scope_note}) — showing "
        f"{offset + 1}-{shown_end}:",
        "| Field | encoded_key | bucket | type | operators |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in page:
        name = e["display_name"]
        if e.get("is_grouped") and e.get("member_sources"):
            name = f"{name} (sources: {', '.join(e['member_sources'])})"
        dtype = e["data_type"] + ("[]" if e.get("is_array") else "")
        ops = ", ".join(s["code"] for s in (e.get("operator_specs") or []))
        lines.append(
            f"| {name} | {e['encoded_key']} | {e['category']} | {dtype} | {ops} |"
        )
    if shown_end < total:
        lines.append(
            f"[{total - shown_end} more — narrow with `search`/`bucket`/`source`, "
            f"or page with `offset`={shown_end} (`limit` ≤{_CATALOG_LIST_MAX}).]"
        )
    lines.append(
        "Operators shown are per-field (the column above is that field's "
        "accepted set); call describe_catalog view='operators' for each "
        "operator's aliases/arity/value type."
    )
    return "\n".join(lines)


def _operators_view() -> str:
    lines = [
        "Filter operators (the per-field accepted set is in describe_catalog "
        "view='fields'). arity: none = no value, one = scalar, list = "
        "array/range.",
        "| code | label (scalar / array) | symbol | arity | value type "
        "| aliases |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for op in OPERATOR_REGISTRY.values():
        label = op.scalar_label
        if op.array_label != op.scalar_label:
            label = f"{op.scalar_label} / {op.array_label}"
        aliases = ", ".join(op.aliases)
        lines.append(
            f"| {op.code} | {label} | {op.symbol or ''} | {op.arity} | "
            f"{op.value_type} | {aliases} |"
        )
    return "\n".join(lines)
