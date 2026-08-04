# SPDX-License-Identifier: AGPL-3.0-or-later
"""operator_specs payload + legacy operators[] removal contract (§5.3, Phase 3)."""

from unittest.mock import MagicMock

import pytest

from src.modules.fields.catalog import FieldCatalog, build_catalog_response


@pytest.fixture
def catalog():
    db = MagicMock()
    db.execute_query.side_effect = lambda sql, _p=None: (
        [{"source_name": "Intune Full", "field_name": "deviceType"}] if "jsonb_each" in sql.lower()
        else [{"tag_name": "Prod"}] if "asset_tags" in sql.lower()
        else [{"id": "secured"}] if "posture_categories" in sql.lower()
        else []
    )
    return FieldCatalog(db_adapter=db)


def _entry(resp, display_name):
    return next(e for e in resp["entries"] if e["display_name"] == display_name)


class TestOperatorSpecs:
    def test_every_entry_has_operator_specs(self, catalog):
        for e in build_catalog_response(catalog)["entries"]:
            assert "operator_specs" in e
            assert isinstance(e["operator_specs"], list) and e["operator_specs"]

    def test_spec_shape(self, catalog):
        spec = _entry(build_catalog_response(catalog), "Category")["operator_specs"][0]
        assert set(spec) == {"code", "label", "symbol", "aliases", "arity", "value_type"}

    def test_default_is_eq(self, catalog):
        assert _entry(build_catalog_response(catalog), "Category")["operator_specs"][0]["code"] == "eq"

    def test_array_field_uses_array_label(self, catalog):
        # Data Source is STRING_ARRAY -> eq label is 'has', not 'equals'.
        ds = _entry(build_catalog_response(catalog), "Data Source")
        eq = next(s for s in ds["operator_specs"] if s["code"] == "eq")
        assert eq["label"] == "has"
        # any -> 'has any of' on arrays
        any_ = next(s for s in ds["operator_specs"] if s["code"] == "any")
        assert any_["label"] == "has any of"
        # all/none present on arrays
        assert {"all", "none"}.issubset({s["code"] for s in ds["operator_specs"]})

    def test_scalar_field_uses_scalar_label(self, catalog):
        cat = _entry(build_catalog_response(catalog), "Category")
        eq = next(s for s in cat["operator_specs"] if s["code"] == "eq")
        assert eq["label"] == "equals"

    def test_aliases_and_full_symbol_set(self, catalog):
        ds = _entry(build_catalog_response(catalog), "Data Source")
        none_ = next(s for s in ds["operator_specs"] if s["code"] == "none")
        assert none_["symbol"] == "NONE"
        assert "without" in none_["aliases"]


class TestVirtualFieldOperatorRestriction:
    """G-1: confidence_level only supports eq/any in its bespoke clause builder,
    so it must offer ONLY those — not the full STRING set (which would silently
    drop neq/none/contains/empty/... and return all rows)."""

    def test_confidence_level_limited_to_eq_any(self, catalog):
        cl = _entry(build_catalog_response(catalog), "Confidence Level")
        codes = [s["code"] for s in cl["operator_specs"]]
        assert codes == ["eq", "any"]

    def test_unrestricted_virtual_keeps_full_set(self, catalog):
        # Data Source Count (INTEGER virtual) is NOT restricted -> full numeric set.
        dsc = _entry(build_catalog_response(catalog), "Data Source Count")
        codes = {s["code"] for s in dsc["operator_specs"]}
        assert {"eq", "gt", "lt", "between"}.issubset(codes)


class TestLegacyOperatorsRemoved:
    """Phase 3 (§11, R2) removed the deprecated operators:string[] wire field
    after its one-release deprecation window — every consumer now reads
    operator_specs. This replaces the former TestFrozenLegacyOperators contract
    (the key no longer exists)."""

    def test_data_source_has_no_legacy_operators_key(self, catalog):
        ds = _entry(build_catalog_response(catalog), "Data Source")
        assert "operators" not in ds
        assert ds["operator_specs"]  # the replacement is present

    def test_category_has_no_legacy_operators_key(self, catalog):
        cat = _entry(build_catalog_response(catalog), "Category")
        assert "operators" not in cat
        assert cat["operator_specs"]
