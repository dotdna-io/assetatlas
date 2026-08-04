# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for static catalog content in src/modules/fields/catalog.py."""

from src.modules.fields.catalog import (
    STATIC_FIELDS,
    CORE_ENRICHMENT_FIELDS,
    CORE_ENRICHMENT_SOURCES,
    FIELD_DATA_TYPES,
    LEGACY_FIELD_MAPPINGS,
    OPERATORS_BY_DATA_TYPE,
    TARGET_FILTER_FIELDS,
    ADVANCED_FIELDS,
    COMPLIANCE_CRITERIA_FIELDS,
    ENRICHMENT_FIELDS,
    TAG_FIELD_PREFIX,
    VIRTUAL_FIELDS,
    normalize_legacy_name,
)
from src.modules.fields.types import FieldDataType, FilterOperator


def test_static_fields_are_known():
    assert STATIC_FIELDS["Hostname"] == "hostname"
    assert STATIC_FIELDS["Confidence Score"] == "confidence"
    assert "OS Normalized" in STATIC_FIELDS


def test_data_source_category_is_a_first_class_static_field():
    assert STATIC_FIELDS["Data Source Category"] == "data_source_category"
    assert FIELD_DATA_TYPES["Data Source Category"] == FieldDataType.STRING_ARRAY
    assert "Data Source Category" in TARGET_FILTER_FIELDS
    assert "Data Source Category" in COMPLIANCE_CRITERIA_FIELDS


def test_core_enrichment_fields_have_path_and_type():
    entry = CORE_ENRICHMENT_FIELDS["Private IP"]
    assert entry["path"] == "AssetAtlas.ip_private"
    assert entry["data_type"] == FieldDataType.JSONB_ARRAY


def test_core_enrichment_sources():
    assert CORE_ENRICHMENT_SOURCES == frozenset({"AssetAtlas"})


def test_legacy_name_mapping():
    assert normalize_legacy_name("OS Type") == "OS Normalized"
    assert normalize_legacy_name("Asset Type") == "Category"
    assert normalize_legacy_name("Hostname") == "Hostname"  # non-legacy passthrough


def test_tag_field_prefix():
    assert TAG_FIELD_PREFIX == "Tags - "


def test_virtual_fields_contains_confidence_level():
    assert "confidence_level" in VIRTUAL_FIELDS
    # Each virtual has a SQL expression string for the resolver
    assert "CASE" in VIRTUAL_FIELDS["confidence_level"]["sql"].upper()


def test_operators_for_string_type():
    ops = OPERATORS_BY_DATA_TYPE[FieldDataType.STRING]
    assert FilterOperator.EQUALS in ops
    assert FilterOperator.IS_NULL in ops


def test_field_lists_shape():
    assert "Hostname" in TARGET_FILTER_FIELDS
    assert "OS EOL Status" in ADVANCED_FIELDS
    assert TARGET_FILTER_FIELDS[0] in COMPLIANCE_CRITERIA_FIELDS
    assert "Public IP" in ENRICHMENT_FIELDS
