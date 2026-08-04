# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/modules/fields/test_resolver.py
"""Tests for FieldResolver: FieldId → ResolvedField + SQL emission."""

from unittest.mock import MagicMock

import pytest

from src.modules.fields.catalog import FieldCatalog
from src.modules.fields.models import (
    InvalidFieldError,
    StaticField, CoreEnrichmentField, ExtendedAttributeField,
    IntegrationGroupField, TagField, PostureField, VirtualField,
)
from src.modules.fields.resolver import FieldResolver
from src.modules.fields.types import FieldDataType


@pytest.fixture
def catalog():
    db = MagicMock()

    def _exec(sql, _params=None):
        lower = sql.lower()
        if "jsonb_each(extended_attributes)" in lower:
            return [
                {"source_name": "Intune Full", "field_name": "deviceEnrollmentType_s"},
                {"source_name": "Jamf A",      "field_name": "deviceType"},
                {"source_name": "Jamf B",      "field_name": "deviceType"},
            ]
        if "asset_tags_v2" in lower:
            return [{"tag_name": "ProductionAsset"}, {"tag_name": "Edge Case'"}]
        if "posture_categories" in lower:
            return [{"id": "secured"}]
        return []

    db.execute_query.side_effect = _exec
    return FieldCatalog(db_adapter=db, group_mapping_provider=lambda: {"Jamf A": "Jamf", "Jamf B": "Jamf"})


@pytest.fixture
def resolver(catalog):
    return FieldResolver(catalog=catalog)


class TestResolveStatic:
    def test_static_resolves_to_column(self, resolver):
        f = StaticField(kind="static", name="Hostname")
        r = resolver.resolve(f)
        assert r.where_expr() == "hostname"
        assert r.data_type == FieldDataType.STRING_ARRAY
        assert r.is_array()

    def test_unknown_static_rejected(self, resolver):
        f = StaticField(kind="static", name="DoesNotExist")
        with pytest.raises(InvalidFieldError):
            resolver.resolve(f)

    def test_static_injection_attempt_rejected(self, resolver):
        f = StaticField(kind="static", name="hostname; DROP TABLE assets")
        with pytest.raises(InvalidFieldError):
            resolver.resolve(f)


class TestResolveCoreEnrichment:
    def test_jsonb_array_path(self, resolver):
        f = CoreEnrichmentField(kind="core_enrichment", name="Public IP")
        r = resolver.resolve(f)
        # JSONB_ARRAY uses the single -> to return jsonb
        assert r.where_expr() == "extended_attributes->'AssetAtlas'->'ip_public'"

    def test_scalar_string_path(self, resolver):
        f = CoreEnrichmentField(kind="core_enrichment", name="IP Type")
        r = resolver.resolve(f)
        # STRING uses ->> to return text
        assert r.where_expr() == "extended_attributes->'AssetAtlas'->>'ip_type'"


class TestResolveExtendedAttribute:
    def test_single_instance(self, resolver):
        f = ExtendedAttributeField(kind="extended_attribute", source="Intune Full", attribute="deviceEnrollmentType_s")
        r = resolver.resolve(f)
        assert r.where_expr() == "extended_attributes->'Intune Full'->>'deviceEnrollmentType_s'"

    def test_unknown_source_rejected(self, resolver):
        f = ExtendedAttributeField(kind="extended_attribute", source="NotASource", attribute="deviceType")
        with pytest.raises(InvalidFieldError):
            resolver.resolve(f)


class TestResolveIntegrationGroup:
    def test_coalesce_sorted_members(self, resolver):
        f = IntegrationGroupField(kind="integration_group", group="Jamf", attribute="deviceType")
        r = resolver.resolve(f)
        # Members sorted (Jamf A before Jamf B)
        assert r.where_expr() == (
            "COALESCE("
            "extended_attributes->'Jamf A'->>'deviceType', "
            "extended_attributes->'Jamf B'->>'deviceType'"
            ")"
        )


class TestResolveTag:
    def test_tag_boolean_expr(self, resolver):
        f = TagField(kind="tag", name="ProductionAsset")
        r = resolver.resolve(f)
        assert r.where_expr() == "tags->>'ProductionAsset'"

    def test_tag_with_single_quote_escaped(self, resolver):
        # Tag "Edge Case'" is present in the catalog (from fixture)
        f = TagField(kind="tag", name="Edge Case'")
        r = resolver.resolve(f)
        # Single quote is doubled for SQL safety
        assert r.where_expr() == "tags->>'Edge Case'''"

    def test_unknown_tag_rejected(self, resolver):
        f = TagField(kind="tag", name="NotATag")
        with pytest.raises(InvalidFieldError):
            resolver.resolve(f)


class TestResolvePosture:
    def test_state_path(self, resolver):
        f = PostureField(kind="posture", category="secured", read="state")
        r = resolver.resolve(f)
        assert r.where_expr() == "posture_rules->'secured'->>'state'"

    def test_output_path(self, resolver):
        f = PostureField(kind="posture", category="secured", read="output")
        r = resolver.resolve(f)
        assert r.where_expr() == "posture_rules->'secured'->>'output'"

    def test_unknown_category_rejected(self, resolver):
        f = PostureField(kind="posture", category="not_a_category", read="state")
        with pytest.raises(InvalidFieldError):
            resolver.resolve(f)


class TestResolveVirtual:
    def test_confidence_level_case_expr(self, resolver):
        f = VirtualField(kind="virtual", name="confidence_level")
        r = resolver.resolve(f)
        assert "CASE" in r.where_expr().upper()
        assert "confidence" in r.where_expr()


class TestSelectExprWithAlias:
    def test_alias_adds_AS_clause(self, resolver):
        f = StaticField(kind="static", name="Hostname")
        r = resolver.resolve(f)
        assert r.select_expr(alias="static:Hostname") == 'hostname AS "static:Hostname"'

    def test_no_alias_matches_where_expr(self, resolver):
        f = StaticField(kind="static", name="Hostname")
        r = resolver.resolve(f)
        assert r.select_expr() == r.where_expr()

    def test_alias_with_embedded_double_quote_is_escaped(self, resolver):
        f = StaticField(kind="static", name="Hostname")
        r = resolver.resolve(f)
        # A malicious/buggy caller passes an alias containing ". It must be
        # doubled to preserve the SQL identifier structure.
        assert r.select_expr(alias='foo"bar') == 'hostname AS "foo""bar"'

    def test_alias_with_percent_is_escaped_for_psycopg2(self, resolver):
        # encode() emits %NN for spaces / reserved chars. psycopg2 treats %
        # in a SQL string as a paramref marker when execute_query(sql, params)
        # is called, so the alias must double %-signs to preserve them literally.
        f = StaticField(kind="static", name="Hostname")
        r = resolver.resolve(f)
        assert r.select_expr(alias='ext:Entra%20Devices:attr') == \
            'hostname AS "ext:Entra%%20Devices:attr"'


class TestSqlEscaping:
    """Verify catalog-sourced strings with single quotes are doubled in SQL output."""

    def test_extended_attribute_source_with_quote_is_escaped(self):
        from unittest.mock import MagicMock
        from src.modules.fields.catalog import FieldCatalog
        from src.modules.fields.resolver import FieldResolver
        from src.modules.fields.models import ExtendedAttributeField

        db = MagicMock()
        db.execute_query.side_effect = lambda sql, _p=None: (
            [{"source_name": "Weird'Source", "field_name": "attr"}] if "jsonb_each" in sql.lower()
            else []
        )
        cat = FieldCatalog(db_adapter=db)
        r = FieldResolver(catalog=cat)
        f = ExtendedAttributeField(kind="extended_attribute", source="Weird'Source", attribute="attr")
        resolved = r.resolve(f)
        assert resolved.where_expr() == "extended_attributes->'Weird''Source'->>'attr'"

    def test_tag_name_with_quote_is_escaped(self, resolver):
        f = TagField(kind="tag", name="Edge Case'")
        resolved = resolver.resolve(f)
        assert "'Edge Case'''" in resolved.where_expr()
