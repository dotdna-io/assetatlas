# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for enrichment Pydantic models."""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from src.modules.enrichment.domain.enrichment_models import (
    EnrichmentDatasetCreate,
    EnrichmentDatasetResponse,
    EnrichmentDatasetStatusUpdate,
    EnrichmentPreviewResponse,
)


# =============================================================================
# EnrichmentDatasetCreate — valid inputs
# =============================================================================


class TestEnrichmentDatasetCreateValid:
    """Tests for valid EnrichmentDatasetCreate construction."""

    def test_exact_match_hostname(self):
        model = EnrichmentDatasetCreate(
            name="Location Mapping",
            match_field="hostname",
            target_field="location",
        )
        assert model.name == "Location Mapping"
        assert model.match_field == "hostname"
        assert model.match_type == "exact"
        assert model.target_field == "location"

    def test_exact_match_all_valid_fields(self):
        """Every allowed match_field value should be accepted."""
        for field in ("hostname", "fqdn", "ip", "mac", "sn", "manufacturer", "model"):
            model = EnrichmentDatasetCreate(
                name="Test",
                match_field=field,
                target_field="enriched_col",
            )
            assert model.match_field == field

    def test_cidr_match_with_ip(self):
        model = EnrichmentDatasetCreate(
            name="Subnet Mapping",
            match_field="ip",
            match_type="cidr",
            target_field="network_zone",
        )
        assert model.match_type == "cidr"
        assert model.match_field == "ip"

    def test_explicit_exact_match_type(self):
        model = EnrichmentDatasetCreate(
            name="SN Lookup",
            match_field="sn",
            match_type="exact",
            target_field="warranty_status",
        )
        assert model.match_type == "exact"


# =============================================================================
# EnrichmentDatasetCreate — CIDR validation
# =============================================================================


class TestEnrichmentDatasetCreateCidr:
    """CIDR match_type must only be used with match_field='ip'."""

    @pytest.mark.parametrize("field", ["hostname", "fqdn", "mac", "sn", "manufacturer", "model"])
    def test_cidr_rejected_for_non_ip_fields(self, field):
        with pytest.raises(ValidationError) as exc_info:
            EnrichmentDatasetCreate(
                name="Bad CIDR",
                match_field=field,
                match_type="cidr",
                target_field="zone",
            )
        assert "cidr" in str(exc_info.value).lower()


# =============================================================================
# EnrichmentDatasetCreate — invalid match_field
# =============================================================================


class TestEnrichmentDatasetCreateInvalidMatchField:
    """Invalid match_field values must be rejected."""

    @pytest.mark.parametrize("bad_field", ["os_type", "category", "", "HOST", "Hostname"])
    def test_invalid_match_field_rejected(self, bad_field):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test",
                match_field=bad_field,
                target_field="col",
            )


# =============================================================================
# EnrichmentDatasetCreate — extended attribute match fields
# =============================================================================


class TestEnrichmentDatasetCreateExtFields:
    """Tests for ext.<source>.<key> match field paths."""

    def test_valid_ext_field_accepted(self):
        model = EnrichmentDatasetCreate(
            name="Entity Mapping",
            match_field="ext.SentinelOne.siteName",
            target_field="entity",
        )
        assert model.match_field == "ext.SentinelOne.siteName"

    def test_ext_field_with_various_sources(self):
        for path in ["ext.Intune.deviceId", "ext.LDAP Computers.dn", "ext.CrowdStrike.aid"]:
            model = EnrichmentDatasetCreate(
                name="Test", match_field=path, target_field="col",
            )
            assert model.match_field == path

    def test_ext_field_too_few_parts_rejected(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test", match_field="ext.SentinelOne", target_field="col",
            )

    def test_ext_field_too_many_parts_rejected(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test", match_field="ext.a.b.c", target_field="col",
            )

    def test_ext_field_empty_parts_rejected(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test", match_field="ext..key", target_field="col",
            )

    def test_cidr_with_ext_field_rejected(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test",
                match_field="ext.SentinelOne.siteName",
                match_type="cidr",
                target_field="col",
            )


# =============================================================================
# EnrichmentDatasetCreate — canonical encoded field ids (the fixed UI's output)
# =============================================================================


class TestEnrichmentDatasetCreateCanonicalFields:
    """Canonical encoded field ids are accepted for core AND data-source fields;
    tag/posture/virtual kinds are out of scope and rejected."""

    @pytest.mark.parametrize("encoded", [
        "static:Hostname",
        "static:IP%20Address",
        "ext:SentinelOne:siteName",
        "group:Entra:building",          # integration-group field
        "core:Private%20IP",
    ])
    def test_canonical_field_accepted(self, encoded):
        model = EnrichmentDatasetCreate(
            name="Test", match_field=encoded, target_field="col",
        )
        assert model.match_field == encoded

    def test_integration_group_field_accepted(self):
        # Selecting a grouped integration attribute must no longer be rejected —
        # this is exactly what failed before the fix (it arrived as the bare
        # literal "integration_group" and 400'd).
        model = EnrichmentDatasetCreate(
            name="Site map", match_field="group:Entra:building", target_field="site",
        )
        assert model.match_field == "group:Entra:building"

    @pytest.mark.parametrize("encoded", [
        "tag:production",
        "posture_state:patching",
        "virtual:confidence_level",
        "integration_group",  # the bare kind name the old UI mistakenly sent
    ])
    def test_unsupported_or_malformed_field_rejected(self, encoded):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test", match_field=encoded, target_field="col",
            )

    def test_canonical_ip_allows_cidr(self):
        model = EnrichmentDatasetCreate(
            name="Subnets", match_field="static:IP%20Address",
            match_type="cidr", target_field="zone",
        )
        assert model.match_type == "cidr"

    def test_cidr_rejected_for_canonical_non_ip(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test", match_field="static:Hostname",
                match_type="cidr", target_field="zone",
            )


# =============================================================================
# EnrichmentDatasetCreate — name validation
# =============================================================================


class TestEnrichmentDatasetCreateNameValidation:

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="",
                match_field="hostname",
                target_field="location",
            )

    def test_name_max_length_exceeded(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="x" * 256,
                match_field="hostname",
                target_field="location",
            )

    def test_name_at_max_length(self):
        model = EnrichmentDatasetCreate(
            name="x" * 255,
            match_field="hostname",
            target_field="location",
        )
        assert len(model.name) == 255


# =============================================================================
# EnrichmentDatasetCreate — target_field validation & sanitization
# =============================================================================


class TestEnrichmentDatasetCreateTargetField:

    def test_empty_target_field_rejected(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test",
                match_field="hostname",
                target_field="",
            )

    def test_target_field_max_length_exceeded(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test",
                match_field="hostname",
                target_field="x" * 101,
            )

    def test_target_field_sanitized_to_lowercase(self):
        model = EnrichmentDatasetCreate(
            name="Test",
            match_field="hostname",
            target_field="MyField",
        )
        assert model.target_field == "myfield"

    def test_target_field_sanitized_spaces_to_underscores(self):
        model = EnrichmentDatasetCreate(
            name="Test",
            match_field="hostname",
            target_field="My Field Name!",
        )
        # "!" becomes "_", then trailing underscore is stripped
        assert model.target_field == "my_field_name"

    def test_target_field_sanitized_special_chars(self):
        model = EnrichmentDatasetCreate(
            name="Test",
            match_field="hostname",
            target_field="  Some--Field!!Name  ",
        )
        assert model.target_field == "some_field_name"

    def test_target_field_sanitized_collapses_underscores(self):
        model = EnrichmentDatasetCreate(
            name="Test",
            match_field="hostname",
            target_field="a___b",
        )
        assert model.target_field == "a_b"

    def test_target_field_sanitized_strips_leading_trailing_underscores(self):
        model = EnrichmentDatasetCreate(
            name="Test",
            match_field="hostname",
            target_field="__field__",
        )
        assert model.target_field == "field"

    def test_target_field_all_special_chars_rejected(self):
        """A target_field that sanitizes to empty should be rejected."""
        with pytest.raises(ValidationError):
            EnrichmentDatasetCreate(
                name="Test",
                match_field="hostname",
                target_field="!!!",
            )


# =============================================================================
# EnrichmentDatasetResponse
# =============================================================================


class TestEnrichmentDatasetResponse:
    """Response model should accept well-formed data."""

    def test_construct_from_dict(self):
        data = {
            "id": "ds_abc123",
            "name": "Location Mapping",
            "match_field": "hostname",
            "match_type": "exact",
            "target_field": "location",
            "status": "active",
            "row_count": 150,
            "matched_count": 120,
            "uploaded_by": "admin",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
        model = EnrichmentDatasetResponse(**data)
        assert model.id == "ds_abc123"
        assert model.name == "Location Mapping"
        assert model.row_count == 150
        assert model.matched_count == 120
        assert model.uploaded_by == "admin"

    def test_optional_fields_default_none(self):
        data = {
            "id": "ds_abc123",
            "name": "Test",
            "match_field": "ip",
            "match_type": "cidr",
            "target_field": "zone",
            "status": "inactive",
            "row_count": 0,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        model = EnrichmentDatasetResponse(**data)
        assert model.matched_count is None
        assert model.uploaded_by is None


# =============================================================================
# EnrichmentDatasetStatusUpdate
# =============================================================================


class TestEnrichmentDatasetStatusUpdate:

    def test_valid_active(self):
        model = EnrichmentDatasetStatusUpdate(status="active")
        assert model.status == "active"

    def test_valid_inactive(self):
        model = EnrichmentDatasetStatusUpdate(status="inactive")
        assert model.status == "inactive"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            EnrichmentDatasetStatusUpdate(status="deleted")


# =============================================================================
# EnrichmentPreviewResponse
# =============================================================================


class TestEnrichmentPreviewResponse:

    def test_construct_preview_response(self):
        model = EnrichmentPreviewResponse(
            dataset_id="ds_abc123",
            dataset_name="Location Mapping",
            matched_count=42,
            total_rows=100,
            sample_matches=[
                {"hostname": "srv-01", "location": "NYC"},
                {"hostname": "srv-02", "location": "LAX"},
            ],
        )
        assert model.dataset_id == "ds_abc123"
        assert model.matched_count == 42
        assert model.total_rows == 100
        assert len(model.sample_matches) == 2

    def test_empty_sample_matches(self):
        model = EnrichmentPreviewResponse(
            dataset_id="ds_abc123",
            dataset_name="Test",
            matched_count=0,
            total_rows=50,
            sample_matches=[],
        )
        assert model.sample_matches == []
