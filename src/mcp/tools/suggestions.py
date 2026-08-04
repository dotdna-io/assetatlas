# SPDX-License-Identifier: AGPL-3.0-or-later
"""Semantic suggestion tools for the AssetAtlas MCP server.

Tools: semantic_suggest
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext

logger = logging.getLogger(__name__)


def _get_suggestion_service(ctx: Context[ServerSession, AppContext]):
    return ctx.request_context.lifespan_context.suggestion_service


def register_tools(mcp):
    """Register semantic-suggestion tools on the FastMCP server."""

    @mcp.tool()
    def semantic_suggest(
        query: str,
        limit: int = 8,
        min_score: float = 0.5,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Resolve user terminology to AssetAtlas fields via semantic search.

        Use this when the user's phrase (e.g. "machine name", "department",
        "patch level") does not obviously match a field in the schema.

        Args:
            query: User phrase (min 2 chars).
            limit: Max suggestions (1-20, default 8).
            min_score: Minimum similarity score (0.0-1.0, default 0.5).
        """
        return semantic_suggest_impl(
            _get_suggestion_service(ctx),
            query=query, limit=limit, min_score=min_score,
        )


def semantic_suggest_impl(service, *, query: str, limit: int, min_score: float) -> str:
    if service is None:
        return "Suggestion service is not available in this deployment."

    limit = max(1, min(limit, 20))
    min_score = max(0.0, min(min_score, 1.0))

    try:
        result: Dict[str, Any] = service.get_suggestions(
            query=query, limit=limit, include_values=False, min_score=min_score,
        ) or {}
    except Exception as e:
        logger.warning("semantic_suggest: get_suggestions raised: %s", e)
        return "Suggestion service encountered an error. Please try again."

    if result.get("service_unavailable"):
        reason = result.get("message") or "Suggestion service unavailable."
        return reason

    suggestions = result.get("suggestions") or []
    if not suggestions:
        msg = result.get("message")
        suffix = f": {msg}" if msg else "."
        return f"No suggestions for {query!r}{suffix}"

    lines = [f"Suggestions for {query!r}:"]
    for s in suggestions:
        name = s.get("field_name") or s.get("value") or "?"
        kind = s.get("type", "field")
        score = s.get("score", 0.0)
        display = s.get("display_name")
        suffix = f" ({display})" if display and display != name else ""
        lines.append(f"- {name}{suffix} [type={kind}, score={score:.2f}]")
    return "\n".join(lines)
