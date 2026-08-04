# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for query engine handling of boolean tag fields."""
import pytest
from unittest.mock import MagicMock
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.tag_rules.services.query_engine_service import QueryEngineService


def _make_resolver(tag_names=None):
    """Build a FieldResolver whose catalog knows the given tag names."""
    db = MagicMock()
    tag_rows = [{"tag_name": t} for t in (tag_names or [])]

    def _execute_query(sql, params):
        sql_s = sql.strip()
        if "asset_tags_v2" in sql_s or "tag_name" in sql_s:
            return tag_rows
        if "posture_categories" in sql_s:
            return []
        return []

    db.execute_query.side_effect = _execute_query
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _make_engine(tag_names=None):
    return QueryEngineService(resolver=_make_resolver(tag_names=tag_names))


class TestQueryEngineTagFields:
    """Tests for building SQL from tag field filters."""

    def test_tag_equals_true_generates_is_not_null(self):
        """'TagField(Windows-Server) = true' should generate IS NOT NULL check."""
        engine = _make_engine(tag_names=["Windows-Server"])
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "tag", "name": "Windows-Server"}, "operator": "equals", "value": "true"}
        ])
        assert "IS NOT NULL" in clause
        assert "tags->>'Windows-Server'" in clause
        assert params == {}

    def test_tag_equals_false_generates_is_null(self):
        """'TagField(Windows-Server) = false' should generate IS NULL check."""
        engine = _make_engine(tag_names=["Windows-Server"])
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "tag", "name": "Windows-Server"}, "operator": "equals", "value": "false"}
        ])
        assert "IS NULL" in clause
        assert "tags->>'Windows-Server'" in clause
        assert params == {}

    def test_tag_is_empty_generates_is_null(self):
        """'TagField(AV-Protected) is empty' should generate IS NULL."""
        engine = _make_engine(tag_names=["AV-Protected"])
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "tag", "name": "AV-Protected"}, "operator": "is empty", "value": ""}
        ])
        assert "IS NULL" in clause
        assert params == {}

    def test_tag_is_not_empty_generates_is_not_null(self):
        """'TagField(AV-Protected) is not empty' should generate IS NOT NULL."""
        engine = _make_engine(tag_names=["AV-Protected"])
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "tag", "name": "AV-Protected"}, "operator": "is not empty", "value": ""}
        ])
        assert "IS NOT NULL" in clause
        assert params == {}

    def test_tag_combined_with_standard_field(self):
        """Tag filter should combine with standard field using AND."""
        engine = _make_engine(tag_names=["AV-Protected"])
        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "Server"},
            {"field_id": {"kind": "tag", "name": "AV-Protected"}, "operator": "equals", "value": "true"},
        ])
        assert "AND" in clause
        assert "category" in clause
        assert "tags->>'AV-Protected'" in clause

    def test_extended_field_still_works(self):
        """Existing extended attribute JSONB handling should not be affected."""
        db = MagicMock()

        def _execute_query(sql, params):
            sql_s = sql.strip()
            if "asset_tags_v2" in sql_s or "tag_name" in sql_s:
                return []
            if "posture_categories" in sql_s:
                return []
            # extended_attributes query
            return [{"source_name": "CrowdStrike", "field_name": "software"}]

        db.execute_query.side_effect = _execute_query
        resolver = FieldResolver(catalog=FieldCatalog(db_adapter=db))
        engine = QueryEngineService(resolver=resolver)

        clause, params = engine.build_where_clause([
            {"field_id": {"kind": "extended_attribute", "source": "CrowdStrike", "attribute": "software"}, "operator": "contains", "value": "Chrome"}
        ])
        assert "extended_attributes" in clause
        assert "ILIKE" in clause
