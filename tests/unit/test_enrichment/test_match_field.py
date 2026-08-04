# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for enrichment match_field -> FieldId resolution (canonical + legacy)."""

import pytest

from src.modules.enrichment.domain.match_field import (
    match_field_to_field_id,
    is_allowed_match_kind,
    is_ip_field,
    ALLOWED_MATCH_KINDS,
)
from src.modules.fields.models import (
    StaticField,
    CoreEnrichmentField,
    ExtendedAttributeField,
    IntegrationGroupField,
    TagField,
    PostureField,
    VirtualField,
)


class TestCanonicalEncodedForms:
    """The canonical encoded field-id strings the (fixed) UI submits."""

    def test_static_field(self):
        assert match_field_to_field_id("static:Hostname") == StaticField(
            kind="static", name="Hostname"
        )

    def test_static_field_with_space_is_percent_decoded(self):
        assert match_field_to_field_id("static:IP%20Address") == StaticField(
            kind="static", name="IP Address"
        )

    def test_extended_attribute_field(self):
        assert match_field_to_field_id("ext:SentinelOne:siteName") == ExtendedAttributeField(
            kind="extended_attribute", source="SentinelOne", attribute="siteName"
        )

    def test_integration_group_field(self):
        # The bug case: picking a grouped integration field must yield a real
        # IntegrationGroupField, not the literal kind name "integration_group".
        assert match_field_to_field_id("group:Entra:building") == IntegrationGroupField(
            kind="integration_group", group="Entra", attribute="building"
        )

    def test_integration_group_field_with_space_in_group(self):
        assert match_field_to_field_id("group:Entra%20Devices:building") == IntegrationGroupField(
            kind="integration_group", group="Entra Devices", attribute="building"
        )

    def test_core_enrichment_field(self):
        assert match_field_to_field_id("core:Private%20IP") == CoreEnrichmentField(
            kind="core_enrichment", name="Private IP"
        )


class TestLegacyBareCoreColumns:
    """Datasets created before canonical encoding stored bare column names."""

    @pytest.mark.parametrize("column,display", [
        ("hostname", "Hostname"),
        ("fqdn", "FQDN"),
        ("ip", "IP Address"),
        ("mac", "MAC Address"),
        ("sn", "Serial Number"),
        ("manufacturer", "Manufacturer"),
        ("model", "Model"),
    ])
    def test_legacy_core_column_maps_to_static(self, column, display):
        assert match_field_to_field_id(column) == StaticField(kind="static", name=display)


class TestLegacyExtForm:
    """Legacy ext.<source>.<key> dotted form must still resolve."""

    def test_legacy_ext_field(self):
        assert match_field_to_field_id("ext.SentinelOne.siteName") == ExtendedAttributeField(
            kind="extended_attribute", source="SentinelOne", attribute="siteName"
        )

    def test_legacy_ext_field_source_with_space(self):
        assert match_field_to_field_id("ext.LDAP Computers.dn") == ExtendedAttributeField(
            kind="extended_attribute", source="LDAP Computers", attribute="dn"
        )


class TestUnrecognized:
    """Garbage / unsupported strings raise ValueError."""

    @pytest.mark.parametrize("bad", ["", "os_type", "category", "ext.SentinelOne", "ext..key"])
    def test_unrecognized_raises(self, bad):
        with pytest.raises(ValueError):
            match_field_to_field_id(bad)


class TestAllowedKinds:
    """Enrichment may match on core + data-source fields, not tag/posture/virtual."""

    def test_allowed_kinds_set(self):
        assert ALLOWED_MATCH_KINDS == frozenset(
            {"static", "core_enrichment", "extended_attribute", "integration_group"}
        )

    @pytest.mark.parametrize("field_id,allowed", [
        (StaticField(kind="static", name="Hostname"), True),
        (CoreEnrichmentField(kind="core_enrichment", name="Private IP"), True),
        (ExtendedAttributeField(kind="extended_attribute", source="S", attribute="k"), True),
        (IntegrationGroupField(kind="integration_group", group="G", attribute="a"), True),
        (TagField(kind="tag", name="prod"), False),
        (PostureField(kind="posture", category="patching", read="state"), False),
        (VirtualField(kind="virtual", name="confidence_level"), False),
    ])
    def test_is_allowed_match_kind(self, field_id, allowed):
        assert is_allowed_match_kind(field_id) is allowed


class TestIsIpField:
    """CIDR matching is only valid for the core IP Address field."""

    def test_canonical_ip_is_ip_field(self):
        assert is_ip_field(StaticField(kind="static", name="IP Address")) is True

    def test_legacy_ip_resolves_to_ip_field(self):
        assert is_ip_field(match_field_to_field_id("ip")) is True

    def test_hostname_is_not_ip_field(self):
        assert is_ip_field(StaticField(kind="static", name="Hostname")) is False

    def test_ext_field_is_not_ip_field(self):
        assert is_ip_field(
            ExtendedAttributeField(kind="extended_attribute", source="S", attribute="k")
        ) is False
