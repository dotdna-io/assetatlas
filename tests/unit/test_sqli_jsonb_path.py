# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for SQL injection prevention in JSONB path construction.

Covers:
  - FieldResolver — user strings in FieldId are used only to *match* a catalog entry;
    the catalog entry's own strings (from the DB schema) are what appear in SQL.
    Crafted FieldId values that don't match any catalog entry raise InvalidFieldError.
"""

import pytest
from unittest.mock import MagicMock

from src.modules.fields import FieldCatalog, FieldResolver, InvalidFieldError
from src.modules.fields.models import ExtendedAttributeField, TagField
from src.modules.tag_rules.services.query_engine_service import QueryEngineService


# ── FieldResolver — injection boundary ───────────────────────────────────
# (Previous tests targeted AssetRepository._resolve_filter_column — that
# method was removed in issue #455 Chunk 2. The injection boundary now
# lives entirely in the FieldResolver via catalog matching, tested below.)

class TestFieldResolverSQLiBoundary:
    """FieldResolver prevents injection by rejecting unknown FieldIds.

    The resolver looks up FieldId values in the catalog index built from
    trusted DB schema rows.  A crafted FieldId (e.g. with SQL in its name)
    that does not match any real catalog entry raises InvalidFieldError —
    the malicious string never reaches SQL construction.
    """

    @pytest.fixture
    def resolver(self):
        """Resolver with a simple catalog (no ext attrs, no tags)."""
        db = MagicMock()
        db.execute_query.return_value = []
        return FieldResolver(catalog=FieldCatalog(db_adapter=db))

    def test_unknown_extended_attribute_raises(self, resolver):
        """A crafted extended_attribute FieldId that isn't in the catalog raises."""
        malicious = ExtendedAttributeField(
            kind="extended_attribute",
            source="Foo' OR 1=1--",
            attribute="bar"
        )
        with pytest.raises(InvalidFieldError):
            resolver.resolve(malicious)

    def test_unknown_tag_raises(self, resolver):
        """A crafted tag FieldId that isn't in the catalog raises."""
        malicious = TagField(kind="tag", name="'; DROP TABLE assets;--")
        with pytest.raises(InvalidFieldError):
            resolver.resolve(malicious)

    def test_known_ext_attr_produces_escaped_sql(self):
        """A real catalog entry with a single quote in its name is escaped by the resolver."""
        # Simulate DB returning a source name that itself contains a quote
        # (edge case: DB schema has unusual names).
        db = MagicMock()
        source_with_quote = "O'Brien Tools"
        attr = "device_type"

        def _execute_query(sql, params):
            sql_s = sql.strip()
            if "asset_tags_v2" in sql_s or "tag_name" in sql_s:
                return []
            if "posture_categories" in sql_s:
                return []
            return [{"source_name": source_with_quote, "field_name": attr}]

        db.execute_query.side_effect = _execute_query
        resolver = FieldResolver(catalog=FieldCatalog(db_adapter=db))

        field_id = ExtendedAttributeField(
            kind="extended_attribute",
            source=source_with_quote,
            attribute=attr,
        )
        resolved = resolver.resolve(field_id)
        sql_expr = resolved.where_expr()

        # The single quote in the source name must be doubled in the SQL expression
        assert "''" in sql_expr
        assert "O''Brien Tools" in sql_expr
        # The raw single quote must not appear unescaped
        assert "O'Brien" not in sql_expr.replace("O''Brien", "")


# ── PostureOutputField injection boundary ─────────────────────────────────

class TestPostureOutputFieldInjection:
    """Regression: output_value in field mode must be a typed FieldId,
    not a raw string.  Closes the CVE #455 injection path via get_db_column.
    """

    def test_output_field_id_required_when_field_mode(self):
        """PostureSubRuleCreate must reject field mode without output_field_id."""
        from pydantic import ValidationError
        from src.modules.posture_rules.models.rule import PostureSubRuleCreate
        with pytest.raises(ValidationError):
            PostureSubRuleCreate(
                name="bad",
                target_filters=[
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                compliance_criteria={"logic": "any", "conditions": [
                    {"field_id": {"kind": "static", "name": "Domain"}, "operator": "is not empty", "value": ""},
                ]},
                output_mode="field",
                # output_field_id omitted — model_validator must raise
            )

    def test_output_field_id_must_be_typed_not_plain_string(self):
        """A plain SQL injection string cannot be passed as output_field_id."""
        from pydantic import ValidationError
        from src.modules.posture_rules.models.rule import PostureSubRuleCreate
        with pytest.raises(ValidationError):
            PostureSubRuleCreate(
                name="bad",
                target_filters=[
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                compliance_criteria={"logic": "any", "conditions": [
                    {"field_id": {"kind": "static", "name": "Domain"}, "operator": "is not empty", "value": ""},
                ]},
                output_mode="field",
                output_field_id="COALESCE(id::text, (SELECT current_setting('app.secret')))",  # type: ignore[arg-type]
            )

    def test_fetch_asset_field_values_raises_for_unknown_field(self):
        """fetch_asset_field_values propagates InvalidFieldError for non-live FieldIds."""
        from src.modules.fields import InvalidFieldError
        from src.modules.fields.models import StaticField
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository

        repo = PostureRepository(db_adapter=MagicMock())
        resolver = MagicMock()
        resolver.resolve.side_effect = InvalidFieldError("unknown field")

        field_id = StaticField(kind="static", name="NonExistentField")
        with pytest.raises(InvalidFieldError):
            repo.fetch_asset_field_values(
                ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"], field_id, resolver
            )

    def test_fetch_asset_field_values_sql_comes_from_resolver_not_user(self):
        """SQL expression comes from the trusted resolver, not from any user string."""
        from src.modules.fields.models import StaticField
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository

        db = MagicMock()
        db.execute_query.return_value = []
        repo = PostureRepository(db_adapter=db)

        resolver = MagicMock()
        resolved = MagicMock()
        resolved.where_expr.return_value = "hostname"  # trusted catalog expression
        resolved.data_type = None
        # Importing here to avoid circular at module level
        from src.modules.fields.types import FieldDataType
        resolved.data_type = FieldDataType.STRING

        resolver.resolve.return_value = resolved

        field_id = StaticField(kind="static", name="Hostname")
        repo.fetch_asset_field_values(
            ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"], field_id, resolver,
        )

        called_sql = db.execute_query.call_args[0][0]
        # The SQL must use the catalog expression, not any user string
        assert "hostname" in called_sql
        # The user's field_id.name ("Hostname") must NOT appear literally in SQL
        assert "Hostname" not in called_sql


# ── _navigate_jsonb_path — escaped-quote round-trip ──────────────────────

class TestNavigateJsonbPathEscapedQuotes:
    """_navigate_jsonb_path must correctly un-double quotes from _escape_literal.

    _escape_literal doubles single quotes (O'Brien → O''Brien) when building
    SQL expressions.  When the navigator parses that SQL expression back to
    traverse an in-memory asset dict, it must un-double those quotes so the
    dict key lookup uses the original (single-quote) key.
    """

    def test_navigate_jsonb_path_with_escaped_quotes(self):
        """Source name with a single quote round-trips correctly through the navigator."""
        engine = QueryEngineService.__new__(QueryEngineService)  # bypass __init__
        asset = {"extended_attributes": {"O'Brien Tools": {"deviceType": "laptop"}}}
        # SQL expression has the doubled quote from _escape_literal
        db_column = "extended_attributes->'O''Brien Tools'->>'deviceType'"
        assert engine._navigate_jsonb_path(asset, db_column) == "laptop"

    def test_navigate_jsonb_path_no_quotes(self):
        """Plain key (no quotes) still works after the refactor."""
        engine = QueryEngineService.__new__(QueryEngineService)
        asset = {"extended_attributes": {"Jamf": {"osVersion": "14.0"}}}
        db_column = "extended_attributes->>'Jamf'"
        assert engine._navigate_jsonb_path(asset, db_column) == {"osVersion": "14.0"}

    def test_navigate_jsonb_path_nested(self):
        """Three-level JSONB path with a quoted intermediate key resolves correctly."""
        engine = QueryEngineService.__new__(QueryEngineService)
        asset = {"extended_attributes": {"Src'A": {"sub": "value"}}}
        db_column = "extended_attributes->'Src''A'->>'sub'"
        assert engine._navigate_jsonb_path(asset, db_column) == "value"
