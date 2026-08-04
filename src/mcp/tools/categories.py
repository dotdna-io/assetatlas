# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asset-category tools for the AssetAtlas MCP server.

Tools: get_asset_categories
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext

logger = logging.getLogger(__name__)

_CATEGORIES_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "categories.json")
)


def register_tools(mcp):
    """Register asset-category tools on the FastMCP server."""

    @mcp.tool()
    def get_asset_categories(
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List all valid asset categories and their subcategories.

        Returns the canonical category/subcategory pairs used by the pipeline
        classifier and the Explorer reclassify UI. Use this when the user
        asks what categories exist, or when they mention a value and you
        need to verify whether it is a valid category or subcategory.
        """
        return get_asset_categories_impl()


def get_asset_categories_impl(
    *,
    path: Optional[str] = None,
    categories: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Render the categories list as Markdown.

    Either `categories` (pre-loaded list) or `path` (JSON file to read) may be
    provided. With no arguments, reads from the default data/categories.json.
    """
    if categories is None:
        categories = _load_categories(path or _CATEGORIES_PATH)

    if not categories:
        return "No asset categories defined."

    lines = ["# Asset categories"]
    for cat in categories:
        name = cat.get("name") or "?"
        subs = cat.get("subcategories") or []
        if subs:
            lines.append(f"- **{name}**: {', '.join(subs)}")
        else:
            lines.append(f"- **{name}**")
    return "\n".join(lines)


@lru_cache(maxsize=4)
def _load_categories(path: str) -> List[Dict[str, Any]]:
    """Load and cache categories from a JSON file. Callers must not mutate the result."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("Categories config not found at %s", path)
        return []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load categories config %s: %s", path, e)
        return []
    cats = data.get("categories", [])
    if not isinstance(cats, list):
        logger.warning("Categories config at %s has unexpected shape", path)
        return []
    return cats
