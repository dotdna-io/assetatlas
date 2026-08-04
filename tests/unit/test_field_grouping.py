# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for FieldGroupResolver."""

from src.modules.integrations.utils.field_grouping import FieldGroupResolver


class TestResolveSource:
    """FieldGroupResolver.resolve_source()"""

    def test_grouped_instance_returns_group_name(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra", "Entra - B": "Entra"})
        assert resolver.resolve_source("Entra - A") == "Entra"
        assert resolver.resolve_source("Entra - B") == "Entra"

    def test_ungrouped_instance_returns_itself(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra"})
        assert resolver.resolve_source("Intune") == "Intune"

    def test_empty_mapping_returns_itself(self):
        resolver = FieldGroupResolver({})
        assert resolver.resolve_source("Entra - A") == "Entra - A"


class TestDeduplicateSources:
    """FieldGroupResolver.deduplicate_sources()"""

    def test_grouped_instances_collapse(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra", "Entra - B": "Entra"})
        result = resolver.deduplicate_sources(["Entra - A", "Entra - B", "Intune"])
        assert result == ["Entra", "Intune"]

    def test_preserves_first_occurrence_order(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra", "Entra - B": "Entra"})
        result = resolver.deduplicate_sources(["Intune", "Entra - A", "Entra - B"])
        assert result == ["Intune", "Entra"]

    def test_ungrouped_pass_through(self):
        resolver = FieldGroupResolver({})
        result = resolver.deduplicate_sources(["Intune", "CrowdStrike"])
        assert result == ["Intune", "CrowdStrike"]

    def test_empty_list(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra"})
        assert resolver.deduplicate_sources([]) == []

    def test_single_grouped_instance(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra"})
        result = resolver.deduplicate_sources(["Entra - A"])
        assert result == ["Entra"]

    def test_none_input(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra"})
        assert resolver.deduplicate_sources(None) == []


class TestGroupExtendedFields:
    """FieldGroupResolver.group_extended_fields()"""

    def test_common_fields_grouped(self):
        """Fields present in ALL group members unify under group name."""
        resolver = FieldGroupResolver({"Entra - A": "Entra", "Entra - B": "Entra"})
        fields_by_source = {
            "Entra - A": ["device_trust_level", "compliance_state"],
            "Entra - B": ["device_trust_level", "compliance_state"],
        }
        result = resolver.group_extended_fields(fields_by_source)
        # Common fields under group name
        assert "Entra" in result
        entra_fields = [f["field"] for f in result["Entra"]]
        assert "device_trust_level" in entra_fields
        assert "compliance_state" in entra_fields
        # All grouped entries have member_sources and is_grouped
        for entry in result["Entra"]:
            assert entry["is_grouped"] is True
            assert set(entry["member_sources"]) == {"Entra - A", "Entra - B"}
        # No per-instance leftovers
        assert "Entra - A" not in result
        assert "Entra - B" not in result

    def test_non_common_fields_grouped_with_subset_sources(self):
        """Fields not in all members still group, but with fewer member_sources."""
        resolver = FieldGroupResolver({"Entra - A": "Entra", "Entra - B": "Entra"})
        fields_by_source = {
            "Entra - A": ["device_trust_level", "custom_field_x"],
            "Entra - B": ["device_trust_level"],
        }
        result = resolver.group_extended_fields(fields_by_source)
        assert "Entra" in result
        grouped = {f["field"]: f for f in result["Entra"]}
        # Both fields appear under the group
        assert "device_trust_level" in grouped
        assert "custom_field_x" in grouped
        # device_trust_level has both members
        assert grouped["device_trust_level"]["member_sources"] == ["Entra - A", "Entra - B"]
        # custom_field_x only in A
        assert grouped["custom_field_x"]["member_sources"] == ["Entra - A"]
        # No per-instance keys
        assert "Entra - A" not in result
        assert "Entra - B" not in result

    def test_ungrouped_pass_through(self):
        """Sources not in any group pass through unchanged."""
        resolver = FieldGroupResolver({"Entra - A": "Entra"})
        fields_by_source = {
            "Intune": ["enrollment_type", "compliance_state"],
        }
        result = resolver.group_extended_fields(fields_by_source)
        assert "Intune" in result
        assert len(result["Intune"]) == 2
        for entry in result["Intune"]:
            assert entry["is_grouped"] is False

    def test_partial_grouping(self):
        """A+B grouped, C standalone — all have same field."""
        resolver = FieldGroupResolver({"Entra - A": "Entra", "Entra - B": "Entra"})
        fields_by_source = {
            "Entra - A": ["device_trust_level"],
            "Entra - B": ["device_trust_level"],
            "Entra - C": ["device_trust_level"],
        }
        result = resolver.group_extended_fields(fields_by_source)
        # Group has field
        assert any(f["field"] == "device_trust_level" for f in result.get("Entra", []))
        # Standalone C also has field separately
        assert any(f["field"] == "device_trust_level" for f in result.get("Entra - C", []))

    def test_empty_mapping_no_grouping(self):
        """No groups defined — everything passes through."""
        resolver = FieldGroupResolver({})
        fields_by_source = {
            "Entra - A": ["field1"],
            "Entra - B": ["field1"],
        }
        result = resolver.group_extended_fields(fields_by_source)
        assert "Entra - A" in result
        assert "Entra - B" in result
        assert "Entra" not in result

    def test_empty_fields_by_source(self):
        resolver = FieldGroupResolver({"Entra - A": "Entra"})
        result = resolver.group_extended_fields({})
        assert result == {}

    def test_single_member_group(self):
        """A group with only one member — fields still get group name."""
        resolver = FieldGroupResolver({"Entra - A": "Entra"})
        fields_by_source = {
            "Entra - A": ["device_trust_level"],
        }
        result = resolver.group_extended_fields(fields_by_source)
        assert "Entra" in result
        assert result["Entra"][0]["field"] == "device_trust_level"
        assert result["Entra"][0]["is_grouped"] is True
        assert result["Entra"][0]["member_sources"] == ["Entra - A"]
