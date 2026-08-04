# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alert rules tools for the AssetAtlas MCP server.

Tools: list_alert_rules
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext
from src.mcp.tools._common import truncate

_DESC_TRUNC_CHARS = 80


def register_tools(mcp):
    """Register alert-rules tools on the FastMCP server."""

    @mcp.tool()
    def list_alert_rules(
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List alert rules with type, severity, source, and enabled state."""
        alerts_service = ctx.request_context.lifespan_context.alerts_service
        if alerts_service is None:
            return "Alerts service not available."
        return list_alert_rules_impl(alerts_service)


def list_alert_rules_impl(alerts_service) -> str:
    if alerts_service is None:
        return "Alerts service not available."

    try:
        result = alerts_service.get_all_rules()
    except Exception as e:
        return f"Alert rules unavailable: {e}"

    rules: List[Dict[str, Any]] = (result or {}).get("rules") or []
    if not rules:
        return "No alert rules defined."

    # Group rules by type; missing/None → "other".
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for rule in rules:
        rtype = rule.get("type") or "other"
        groups.setdefault(rtype, []).append(rule)

    lines: List[str] = ["# Alert rules", ""]
    for rtype in sorted(groups.keys()):
        group = groups[rtype]
        lines.append(f"## {rtype} ({len(group)})")
        lines.append("| Name | Severity | Source | Enabled | Description |")
        lines.append("| --- | --- | --- | --- | --- |")
        for rule in group:
            name = rule.get("name") or "—"
            severity = rule.get("severity") or "—"
            source = rule.get("source") or "—"
            enabled = "✓" if rule.get("enabled") else "✗"
            desc = truncate(rule.get("description"), _DESC_TRUNC_CHARS) or "—"
            lines.append(
                f"| {name} | {severity} | {source} | {enabled} | {desc} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


def format_alert_rule_lines(rule: Dict[str, Any]) -> List[str]:
    """Full single-alert-rule definition WITH its id + reusable filters — used
    by the unified ``list_rules`` chat tool. The granular ``list_alert_rules``
    table above drops the id/query/filters/threshold the service already
    returns; this surfaces them so ``preview_rule`` / ``query_assets`` can reuse
    a rule's ``target_filters`` downstream."""
    name = rule.get("name") or "—"
    lines = [f"## {name}  (id={rule.get('id')})"]
    lines.append(
        f"- type: {rule.get('type')} | severity: {rule.get('severity')} | "
        f"source: {rule.get('source')}")
    lines.append(
        f"- enabled: {rule.get('enabled')} | "
        f"evaluation: {rule.get('evaluation_type')} | slug: {rule.get('slug')}")
    if rule.get("description"):
        lines.append(f"- description: {rule['description']}")
    if rule.get("query"):
        lines.append(f"- query: {rule['query']}")
    if rule.get("target_filters"):
        lines.append(f"- target_filters: {json.dumps(rule['target_filters'])}")
    if rule.get("compliance_criteria"):
        lines.append(
            f"- compliance_criteria: {json.dumps(rule['compliance_criteria'])}")
    if rule.get("threshold"):
        lines.append(f"- threshold: {json.dumps(rule['threshold'])}")
    if rule.get("silence_duration") is not None:
        lines.append(f"- silence_duration: {rule['silence_duration']} min")
    prov = []
    if rule.get("created_by"):
        prov.append(f"created_by={rule['created_by']}")
    if rule.get("created_at"):
        prov.append(f"created_at={rule['created_at']}")
    if prov:
        lines.append("- " + " | ".join(prov))
    return lines
