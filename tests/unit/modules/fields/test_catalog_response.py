# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for build_catalog_response — shared /fields endpoint helper."""

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


class TestBuildCatalogResponse:
    def test_returns_entries_key(self, catalog):
        resp = build_catalog_response(catalog)
        assert "entries" in resp
        assert isinstance(resp["entries"], list)

    def test_entry_has_required_keys(self, catalog):
        entry = build_catalog_response(catalog)["entries"][0]
        required = {"display_name", "field_id", "encoded_key", "category",
                    "data_type", "operator_specs", "is_array", "is_grouped",
                    "member_sources"}
        assert required.issubset(entry.keys())

    def test_drops_legacy_operators_key(self, catalog):
        """Phase 3 cleanup: the deprecated operators:string[] field is removed
        from the wire — consumers must use operator_specs (which stays).
        WHY: keeping both invites drift between the frozen legacy labels and the
        per-field resolved labels; the spec mandates removal in Phase 3."""
        resp = build_catalog_response(catalog)
        assert resp["entries"], "expected at least one entry"
        for entry in resp["entries"]:
            assert "operators" not in entry, (
                f"legacy operators key still present on {entry['display_name']}"
            )
            assert "operator_specs" in entry

    def test_include_buckets_filter(self, catalog):
        # When include_buckets=["target", "advanced"], extended_attributes and tags excluded
        resp = build_catalog_response(catalog, include_buckets=["target", "advanced"])
        cats = {e["category"] for e in resp["entries"]}
        assert "extended_attributes" not in cats
        assert "tags" not in cats

    def test_encoded_key_round_trips(self, catalog):
        from src.modules.fields.encoding import decode
        for entry in build_catalog_response(catalog)["entries"]:
            # decode(encoded_key) must match field_id
            decoded = decode(entry["encoded_key"]).model_dump()
            assert decoded == entry["field_id"]
