# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from src.services.embedding.synonym_rules import (
    ABBREVIATION_MAP,
    CORE_FIELD_SYNONYMS,
    EXPLICIT_OVERRIDES,
    match_abbreviations,
    match_core,
    match_overrides,
    match_substring,
    match_rules,
    get_all_core_synonyms,
    RuleMatch,
)


class TestCoreFieldSynonyms:
    """Core field synonyms should cover all 20 static fields comprehensively."""

    def test_all_20_static_fields_have_synonyms(self):
        expected = {
            "hostname", "ip", "mac", "sn", "fqdn", "category", "subcategory",
            "os_normalized", "manufacturer", "model", "location", "owner",
            "data_source", "data_source_category", "first_seen", "last_seen",
            "confidence", "os_eol_status", "ipam_location", "mac_vendor_enriched",
        }
        assert set(CORE_FIELD_SYNONYMS.keys()) == expected

    def test_hostname_has_key_synonyms(self):
        terms = CORE_FIELD_SYNONYMS["hostname"]
        for expected in ["computer name", "machine name", "device name", "server name", "pc name"]:
            assert expected in terms, f"'{expected}' missing from hostname synonyms"

    def test_ip_has_key_synonyms(self):
        terms = CORE_FIELD_SYNONYMS["ip"]
        for expected in ["ip address", "ipv4", "ipv6", "network address"]:
            assert expected in terms

    def test_mac_has_key_synonyms(self):
        terms = CORE_FIELD_SYNONYMS["mac"]
        for expected in ["mac address", "hardware address", "physical address"]:
            assert expected in terms

    def test_serial_has_key_synonyms(self):
        terms = CORE_FIELD_SYNONYMS["sn"]
        for expected in ["serial number", "service tag", "asset tag"]:
            assert expected in terms

    def test_os_has_key_synonyms(self):
        terms = CORE_FIELD_SYNONYMS["os_normalized"]
        for expected in ["operating system", "os", "windows", "linux", "macos"]:
            assert expected in terms

    def test_all_terms_lowercase(self):
        for field, terms in CORE_FIELD_SYNONYMS.items():
            for term in terms:
                assert term == term.lower(), f"Term '{term}' in {field} should be lowercase"

    def test_no_duplicate_terms_across_fields(self):
        """Each term should map to exactly one field."""
        seen = {}
        dupes = []
        for field, terms in CORE_FIELD_SYNONYMS.items():
            for term in terms:
                if term in seen:
                    dupes.append(f"'{term}' in both {seen[term]} and {field}")
                seen[term] = field
        assert dupes == [], f"Duplicate terms: {dupes}"


class TestMatchCore:
    """match_core should resolve core field synonyms instantly."""

    def test_computer_name_resolves_to_hostname(self):
        matches = match_core("computer name")
        assert len(matches) == 1
        assert matches[0].field_name == "hostname"
        assert matches[0].strategy == "core"
        assert matches[0].confidence == 1.0

    def test_mac_address_resolves_to_mac(self):
        matches = match_core("mac address")
        assert len(matches) == 1
        assert matches[0].field_name == "mac"

    def test_serial_number_resolves_to_sn(self):
        matches = match_core("serial number")
        assert len(matches) == 1
        assert matches[0].field_name == "sn"

    def test_unknown_term_returns_empty(self):
        matches = match_core("xyzzy nonsense")
        assert matches == []

    def test_case_insensitive(self):
        matches = match_core("Computer Name")
        assert len(matches) == 1
        assert matches[0].field_name == "hostname"


class TestGetAllCoreSynonyms:
    """get_all_core_synonyms should return flat term→field dict."""

    def test_returns_dict(self):
        result = get_all_core_synonyms()
        assert isinstance(result, dict)
        assert len(result) > 100  # Should have 150+ terms

    def test_hostname_terms_present(self):
        result = get_all_core_synonyms()
        assert result["computer name"] == "hostname"
        assert result["machine name"] == "hostname"

    def test_mac_address_maps_correctly(self):
        result = get_all_core_synonyms()
        assert result["mac address"] == "mac"


class TestMatchRulesIntegration:
    """match_rules should prioritize core > override > abbreviation > substring."""

    def test_core_beats_everything(self):
        """'mac address' is in core synonyms — should resolve there, not via LLM."""
        fields = {
            "mac": "mac - hardware address",
            "ip": "ip - mac address, network address",  # description mentions mac address
        }
        matches = match_rules("mac address", fields)
        assert len(matches) == 1
        assert matches[0].field_name == "mac"
        assert matches[0].strategy == "core"

    def test_override_works_for_extended_fields(self):
        fields = {"sentinelone_detectionState": "detection state info"}
        matches = match_rules("infected", fields)
        assert len(matches) == 1
        assert matches[0].field_name == "sentinelone_detectionState"

    def test_abbreviation_works_when_no_core_or_override(self):
        fields = {"defender_av": "antivirus endpoint protection software"}
        matches = match_rules("av", fields)
        assert len(matches) >= 1
        assert any(m.strategy == "abbreviation" for m in matches)


class TestAbbreviationMap:
    """Abbreviation map for extended fields."""

    def test_av_expands_to_antivirus(self):
        assert "av" in ABBREVIATION_MAP
        assert "antivirus" in ABBREVIATION_MAP["av"]

    def test_edr_expands(self):
        assert "edr" in ABBREVIATION_MAP
        assert "endpoint detection" in ABBREVIATION_MAP["edr"]

    def test_all_keys_lowercase(self):
        for key in ABBREVIATION_MAP:
            assert key == key.lower()


class TestMatchSubstring:
    """Substring matching for extended field descriptions."""

    def test_antivirus_matches_field_description(self):
        fields = {"sentinelone_av": "antivirus software, endpoint protection"}
        matches = match_substring("antivirus", fields)
        assert len(matches) == 1

    def test_short_terms_skipped(self):
        fields = {"os_normalized": "operating system, OS version"}
        matches = match_substring("os", fields)
        assert matches == []
