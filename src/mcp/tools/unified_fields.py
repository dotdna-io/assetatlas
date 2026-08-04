# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unified Fields read tools for the AssetAtlas MCP server.

Unified Fields (Settings → Data → Unified Fields) are user-defined merges of
several catalog fields into one derived field (COALESCE / concat / …). Each one
becomes a queryable ``virtual:<slug>`` catalog field usable in query_assets.

Tools:
- list_unified_fields: the Unified Field catalog — name, merge function, source
  fields, resolved data type, and encoded catalog key.
- get_unified_field: full detail for one Unified Field by its UUID.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.api.middleware.error_handler import NotFoundError
from src.mcp.context import AppContext, get_db, get_resolver
from src.mcp.tools._common import validate_uuid
from src.modules.fields.unified.repository import UnifiedFieldsRepository
from src.modules.fields.unified.service import UnifiedFieldsService


def _unified_fields_service(ctx: Context[ServerSession, AppContext]):
    """Build a UnifiedFieldsService from the MCP app context, mirroring the API
    factory's construction (repo + catalog + resolver + db). The read paths
    (list/get) only touch the repository, but we wire the resolver/catalog too
    so the service object behaves identically to the one the factory builds."""
    db = get_db(ctx)
    resolver = get_resolver(ctx)
    catalog = resolver.catalog() if resolver is not None else None
    return UnifiedFieldsService(
        UnifiedFieldsRepository(db),
        catalog=catalog,
        resolver=resolver,
        db_adapter=db,
    )


def register_tools(mcp):
    """Register Unified Fields read tools on the FastMCP server."""

    @mcp.tool()
    def list_unified_fields(
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List the user-defined Unified Fields (Settings → Data → Unified
        Fields): each field's merge function, source fields, resolved data
        type, and encoded catalog key."""
        return list_unified_fields_impl(_unified_fields_service(ctx))

    @mcp.tool()
    def get_unified_field(
        field_id: str,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Full detail for one Unified Field by its UUID.

        Args:
            field_id: Unified field UUID (from list_unified_fields).
        """
        return get_unified_field_impl(
            _unified_fields_service(ctx), field_id=field_id)


def _format_field(field: Dict[str, Any]) -> List[str]:
    """Render one unified-field dict (UnifiedFieldsService output) as md lines."""
    sources = field.get("sources") or []
    lines = [
        f"## {field.get('name')}  (id={field.get('id')})",
        f"- function: {field.get('function')}",
        f"- data type: {field.get('data_type')}",
        f"- encoded key: {field.get('encoded_key')}",
        f"- sources ({len(sources)}): "
        f"{', '.join(str(s) for s in sources) or '—'}",
    ]
    if field.get("updated_at"):
        lines.append(f"- updated: {field['updated_at']}")
    return lines


def list_unified_fields_impl(service) -> str:
    if service is None:
        return "Unified Fields service not available."
    try:
        fields = service.list()
    except Exception as e:
        return f"Unified Fields unavailable: {e}"
    if not fields:
        return (
            "COUNT: 0 unified fields defined. Create them in "
            "Settings → Data → Unified Fields."
        )
    lines: List[str] = [f"COUNT: {len(fields)} unified field(s).", ""]
    for field in fields:
        lines.extend(_format_field(field))
        lines.append("")
    return "\n".join(lines).rstrip()


def get_unified_field_impl(service, *, field_id: str) -> str:
    if service is None:
        return "Unified Fields service not available."
    err = validate_uuid(field_id, "unified field")
    if err:
        return err
    try:
        field = service.get(field_id)
    except NotFoundError:
        return f"Unified field {field_id} not found."
    except Exception as e:
        return f"Unified field unavailable: {e}"
    return "\n".join(_format_field(field))
