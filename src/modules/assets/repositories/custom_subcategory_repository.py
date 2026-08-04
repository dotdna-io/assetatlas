# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Custom Subcategory Repository

Persists user-added subcategories that extend the static catalog
(``data/categories.json``). Used by ReclassifyService to merge custom
entries into the dropdown returned by GET /api/assets/categories.
"""

import logging
from typing import Dict, List, Optional

import psycopg2

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class CustomSubcategoryRepository:
    """Repository for analyst-added custom subcategories."""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        self.db = db_adapter

    def get_all(self) -> Dict[str, List[str]]:
        """Return ``{category: [subcategory, ...]}`` for every category that
        has at least one custom entry."""
        query = """
            SELECT category, name
              FROM custom_subcategories
          ORDER BY category, lower(name)
        """
        rows = self.db.execute_query(query, fetch=True) or []

        grouped: Dict[str, List[str]] = {}
        for row in rows:
            grouped.setdefault(row["category"], []).append(row["name"])
        return grouped

    def get_for_category(self, category: str) -> List[str]:
        """Return the custom subcategory names for a single category.
        Used by the reclassify validation fast-path to avoid loading
        every category's custom entries when only one is needed.
        """
        query = """
            SELECT name
              FROM custom_subcategories
             WHERE category = %s
          ORDER BY lower(name)
        """
        rows = self.db.execute_query(query, (category,), fetch=True) or []
        return [row["name"] for row in rows]

    def add(self, category: str, name: str, created_by: Optional[str]) -> None:
        """Insert a custom subcategory.

        Raises ``ValueError`` if a duplicate (case-insensitive) already
        exists for the category.
        """
        query = """
            INSERT INTO custom_subcategories (category, name, created_by)
            VALUES (%s, %s, %s)
        """
        try:
            self.db.execute_query(query, (category, name, created_by), fetch=False)
        except psycopg2.errors.UniqueViolation as exc:
            raise ValueError(
                f"Subcategory '{name}' already exists for category '{category}'"
            ) from exc
