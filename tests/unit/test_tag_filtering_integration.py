# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests for tag-based filtering across the system."""
import pytest
from unittest.mock import MagicMock
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.tag_rules.services.query_engine_service import QueryEngineService


def _make_resolver(tag_names=None, ext_attrs=None):
    """Build a FieldResolver whose catalog knows the given tags and extended attributes."""
    db = MagicMock()
    tag_rows = [{"tag_name": t} for t in (tag_names or [])]
    ext_rows = [{"source_name": s, "field_name": f} for s, f in (ext_attrs or [])]

    def _execute_query(sql, params):
        sql_s = sql.strip()
        if "asset_tags_v2" in sql_s or "tag_name" in sql_s:
            return tag_rows
        if "posture_categories" in sql_s:
            return []
        # extended_attributes query
        return ext_rows

    db.execute_query.side_effect = _execute_query
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _make_engine(tag_names=None, ext_attrs=None):
    return QueryEngineService(resolver=_make_resolver(tag_names=tag_names, ext_attrs=ext_attrs))


class TestTagFilteringRoundtrip:
    """End-to-end tests for tag field discovery → mapping → query generation."""

    def test_tag_field_roundtrip(self):
        """Tag field should be discoverable (via FieldCatalog) and queryable."""
        # 1. Build a resolver that knows about "Test-Tag"
        resolver = _make_resolver(tag_names=["Test-Tag"])

        # 2. Verify the catalog knows about it
        index = resolver.catalog().index()
        assert ("tag", "Test-Tag") in index
        entry = index[("tag", "Test-Tag")]
        assert entry.tag_name == "Test-Tag"

        # 3. Query engine generates correct SQL
        engine = QueryEngineService(resolver=resolver)
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "tag", "name": "Test-Tag"}, "operator": "equals", "value": "true"}
        ])
        assert "tags->>'Test-Tag' IS NOT NULL" in clause
        assert params == {}

    def test_tag_field_combined_with_extended_and_standard(self):
        """Tag, extended, and standard field filters should combine correctly."""
        engine = _make_engine(
            tag_names=["AV-Protected"],
            ext_attrs=[("CrowdStrike", "software")]
        )
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "Server"},
            {"field_id": {"kind": "tag", "name": "AV-Protected"}, "operator": "equals", "value": "true"},
            {"field_id": {"kind": "extended_attribute", "source": "CrowdStrike", "attribute": "software"}, "operator": "contains", "value": "Chrome"},
        ])
        assert "AND" in clause
        # Standard field
        assert "category" in clause.lower()
        # Tag field
        assert "tags->>'AV-Protected' IS NOT NULL" in clause
        # Extended field
        assert "extended_attributes" in clause

    def test_negative_tag_filter(self):
        """Filtering for absence of a tag should work."""
        engine = _make_engine(tag_names=["AV-Missing"])
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "tag", "name": "AV-Missing"}, "operator": "equals", "value": "false"}
        ])
        assert "tags->>'AV-Missing' IS NULL" in clause

    def test_multiple_tag_filters(self):
        """Multiple tag filters should combine with AND."""
        engine = _make_engine(tag_names=["Windows-Server", "AV-Protected"])
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "tag", "name": "Windows-Server"}, "operator": "equals", "value": "true"},
            {"field_id": {"kind": "tag", "name": "AV-Protected"}, "operator": "equals", "value": "true"},
        ])
        assert "AND" in clause
        assert "tags->>'Windows-Server' IS NOT NULL" in clause
        assert "tags->>'AV-Protected' IS NOT NULL" in clause
