# SPDX-License-Identifier: AGPL-3.0-or-later
"""Config resources for the AssetAtlas MCP server.

Resources: integrations, tag-rules
"""

import logging

from src.mcp.context import get_app_context

logger = logging.getLogger(__name__)


def register_resources(mcp):
    """Register config resources on the FastMCP server."""

    @mcp.resource("assetatlas://config/integrations")
    def config_integrations() -> str:
        """Configured data source integrations with status and sync info."""
        app_ctx = get_app_context()
        if not app_ctx:
            return "Database not available."

        db = app_ctx.db
        integrations = db.execute_query("""
            SELECT
                i.name, i.integration_type, i.status,
                i.last_sync_at,
                ig.name as group_name,
                COUNT(DISTINCT ap.asset_id) as asset_count
            FROM integrations i
            LEFT JOIN integration_groups ig ON i.group_id = ig.id
            LEFT JOIN asset_providers ap ON ap.data_source = i.name
            GROUP BY i.id, i.name, i.integration_type, i.status,
                     i.last_sync_at, ig.name
            ORDER BY i.status, i.name
        """)

        if not integrations:
            return "No integrations configured."

        lines = ["Configured Integrations\n"]
        lines.append(f"{'Name':25s} {'Type':15s} {'Status':8s} {'Assets':>8s} {'Group':15s} Last Sync")
        lines.append("-" * 100)
        for i in integrations:
            group = i.get("group_name") or ""
            sync = str(i.get("last_sync_at") or "never")[:19]
            lines.append(
                f"{i['name']:25s} {i['integration_type']:15s} {i['status']:8s} "
                f"{i['asset_count']:>8,} {group:15s} {sync}"
            )

        return "\n".join(lines)

    @mcp.resource("assetatlas://config/tag-rules")
    def config_tag_rules() -> str:
        """Tag rule definitions with type, status, and applied tags."""
        app_ctx = get_app_context()
        if not app_ctx:
            return "Database not available."

        db = app_ctx.db
        rules = db.execute_query("""
            SELECT id, name, type, enabled, description, tags,
                   target_filters, execution_count,
                   last_execution_assets_matched
            FROM tag_rules
            ORDER BY type, name
        """)

        if not rules:
            return "No tag rules configured."

        lines = ["Tag Rules Configuration\n"]
        for r in rules:
            status = "enabled" if r.get("enabled") else "disabled"
            rtype = (r.get("type") or "").capitalize()
            lines.append(f"[{status:8s}] {r['name']}  ({rtype})")
            if r.get("description"):
                lines.append(f"  Description: {r['description'][:100]}")

            # Show tags applied
            tags = r.get("tags")
            if tags and isinstance(tags, dict):
                tag_name = tags.get("name") or tags.get("tag_name")
                if tag_name:
                    lines.append(f"  Tags: {tag_name}")

            # Show filter summary
            filters = r.get("target_filters")
            if filters and isinstance(filters, list):
                filter_strs = []
                for f in filters[:3]:
                    if isinstance(f, dict):
                        filter_strs.append(f"{f.get('field', '?')} {f.get('op', '?')} {f.get('value', '?')}")
                if filter_strs:
                    lines.append(f"  Filters: {'; '.join(filter_strs)}")

            if r.get("execution_count"):
                matched = r.get('last_execution_assets_matched', 0)
                lines.append(f"  Runs: {r['execution_count']}, last matched: {matched}")

            lines.append("")

        return "\n".join(lines)
