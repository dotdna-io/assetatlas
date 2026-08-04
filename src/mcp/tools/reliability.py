# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data reliability tool for the AssetAtlas MCP server.

Tools: get_reliability
"""

from __future__ import annotations

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext


def register_tools(mcp):
    """Register data-reliability tools on the FastMCP server."""

    @mcp.tool()
    def get_reliability(
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Return the most and least reliable data source and asset category."""
        dashboard_service = ctx.request_context.lifespan_context.dashboard_service
        if dashboard_service is None:
            return "Reliability service not available."
        return get_reliability_impl(dashboard_service)


def get_reliability_impl(dashboard_service) -> str:
    try:
        data = dashboard_service.get_reliability()
        most_src = data["mostReliableSource"]
        least_src = data["leastReliableSource"]
        most_cat = data["mostReliableCategory"]
        least_cat = data["leastReliableCategory"]
    except Exception as e:
        return f"Reliability data unavailable: {e}"

    # Source scores are raw 0..1 → scale to %. Category scores already 0..100.
    most_src_pct = float(most_src.get("score", 0)) * 100
    least_src_pct = float(least_src.get("score", 0)) * 100
    most_cat_pct = float(most_cat.get("score", 0))
    least_cat_pct = float(least_cat.get("score", 0))

    lines = [
        "# Data reliability",
        "",
        "## Data sources",
        f"- Most reliable: {most_src.get('name')} — {most_src_pct:.1f}%",
        f"- Least reliable: {least_src.get('name')} — {least_src_pct:.1f}%",
        "",
        "## Asset categories",
        f"- Most reliable: {most_cat.get('name')} — {most_cat_pct:.1f}%",
        f"- Least reliable: {least_cat.get('name')} — {least_cat_pct:.1f}%",
    ]
    return "\n".join(lines)
