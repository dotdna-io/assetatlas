# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for FieldCatalog dynamic loading (extended_attributes, tags, posture)."""

from unittest.mock import MagicMock

import pytest

from src.modules.fields.catalog import FieldCatalog


@pytest.fixture
def mock_db():
    """Mock db_adapter whose execute_query returns one preset per query pattern."""
    db = MagicMock()

    def execute_query(sql, _params=None):
        sql_lower = sql.lower()
        if "jsonb_each(extended_attributes)" in sql_lower:
            return [
                {"source_name": "Intune Full", "field_name": "deviceType"},
                {"source_name": "Intune Full", "field_name": "deviceEnrollmentType_s"},
                {"source_name": "Jamf A",      "field_name": "deviceType"},
                {"source_name": "Jamf B",      "field_name": "deviceType"},
                {"source_name": "AssetAtlas",  "field_name": "ip_private"},  # should be filtered out (core)
            ]
        if "asset_tags_v2" in sql_lower and "asset_manual_tags" in sql_lower:
            return [{"tag_name": "ProductionAsset"}, {"tag_name": "PCI-DSS"}]
        if "posture_categories" in sql_lower:
            return [{"id": "secured"}, {"id": "monitored"}]
        return []

    db.execute_query.side_effect = execute_query
    return db


class TestFieldCatalog:
    def test_load_skips_core_enrichment_sources(self, mock_db):
        cat = FieldCatalog(db_adapter=mock_db)
        entries = cat.entries()
        ext_sources = {e.source for e in entries if e.kind_name == "extended_attribute"}
        assert "AssetAtlas" not in ext_sources

    def test_tags_present(self, mock_db):
        cat = FieldCatalog(db_adapter=mock_db)
        tag_names = {e.tag_name for e in entries_by_kind(cat, "tag")}
        assert "ProductionAsset" in tag_names
        assert "PCI-DSS" in tag_names

    def test_posture_yields_state_and_output_entries(self, mock_db):
        cat = FieldCatalog(db_adapter=mock_db)
        posture_entries = entries_by_kind(cat, "posture")
        assert {(e.posture_category, e.posture_read) for e in posture_entries} == {
            ("secured", "state"), ("secured", "output"),
            ("monitored", "state"), ("monitored", "output"),
        }

    def test_grouping_collapses_jamf_instances(self, mock_db):
        cat = FieldCatalog(
            db_adapter=mock_db,
            group_mapping_provider=lambda: {"Jamf A": "Jamf", "Jamf B": "Jamf"},
        )
        group_entries = entries_by_kind(cat, "integration_group")
        # Both Jamf instances share deviceType → collapse to one group entry with members sorted
        jamf = [e for e in group_entries if e.group == "Jamf" and e.attribute == "deviceType"]
        assert len(jamf) == 1
        assert jamf[0].member_sources == ("Jamf A", "Jamf B")

    def test_grouped_members_also_emit_individual_fields(self, mock_db):
        """A grouped integration's own fields stay individually selectable.

        Grouping ADDS a merged group field; it must not HIDE each member's
        per-instance field. A user who grouped their Unifi sites still needs to
        filter on exactly one site (e.g. "Jamf A - deviceType"), which the merged
        COALESCE group field cannot express. Regression guard: the Explorer/rules
        field picker must not lose per-integration fields when integrations are
        grouped.
        """
        cat = FieldCatalog(
            db_adapter=mock_db,
            group_mapping_provider=lambda: {"Jamf A": "Jamf", "Jamf B": "Jamf"},
        )
        ext = {(e.source, e.attribute) for e in entries_by_kind(cat, "extended_attribute")}
        # Each grouped member keeps its own per-instance field...
        assert ("Jamf A", "deviceType") in ext
        assert ("Jamf B", "deviceType") in ext
        # ...alongside (not instead of) the merged group field.
        group = entries_by_kind(cat, "integration_group")
        assert any(e.group == "Jamf" and e.attribute == "deviceType" for e in group)

    def test_ttl_cache_reuses_within_window(self, mock_db):
        cat = FieldCatalog(db_adapter=mock_db, ttl_seconds=60)
        cat.entries()
        cat.entries()  # second call
        # 3 queries per load (extended, tags, posture). Second call reuses cache.
        assert mock_db.execute_query.call_count == 3

    def test_ttl_cache_reloads_after_expiry(self, mock_db):
        cat = FieldCatalog(db_adapter=mock_db, ttl_seconds=0.01)
        cat.entries()
        import time
        time.sleep(0.02)
        cat.entries()
        assert mock_db.execute_query.call_count == 6  # two reloads


def entries_by_kind(cat, kind_name):
    return [e for e in cat.entries() if e.kind_name == kind_name]
