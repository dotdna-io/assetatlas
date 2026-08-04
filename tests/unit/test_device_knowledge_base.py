# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for DeviceKnowledgeBase — Tier 2 of the categorization cascade."""

import pytest

from src.modules.asset_generation.domain.device_knowledge_base import DeviceKnowledgeBase


@pytest.fixture
def sample_kb(tmp_path):
    """Create a minimal KB with test CSV files."""
    (tmp_path / "manufacturers.csv").write_text(
        "manufacturer_normalized,category,subcategory_default,confidence,source\n"
        "fortinet,network,firewall,0.85,nmap\n"
        "canon,printer,laser_printer,0.80,nmap\n"
        "vmware,server,hypervisor,0.90,nmap\n"
    )
    (tmp_path / "product_lines.csv").write_text(
        "manufacturer_normalized,model_pattern,category,subcategory,confidence,source\n"
        "dell,^poweredge,server,physical_server,0.90,netbox\n"
        "dell,^latitude,workstation,laptop,0.90,netbox\n"
        "dell,^optiplex,workstation,desktop,0.90,netbox\n"
        "hp,^proliant,server,physical_server,0.90,netbox\n"
        "hp,^elitebook,workstation,laptop,0.90,netbox\n"
        "hp,^laserjet,printer,laser_printer,0.90,netbox\n"
        "polycom,^vvx,miscellaneous,voip,0.90,manual\n"
        "polycom,^mp5,miscellaneous,voip,0.90,manual\n"
        "3com,^enterprise,network,wireless_access_point,0.9,recog\n"
    )
    (tmp_path / "manufacturer_aliases.csv").write_text(
        "alias_pattern,manufacturer_normalized\n"
        "hewlett.packard|hp inc|hp enterprise,hp\n"
        "dell technologies|dell inc|dell emc,dell\n"
        "fortinet.*inc,fortinet\n"
    )
    (tmp_path / "oui_categories.csv").write_text(
        "oui_prefix,manufacturer_normalized,category,source\n"
        "005056,vmware,server,ieee_oui\n"
        "001A2B,canon,printer,ieee_oui\n"
    )
    return DeviceKnowledgeBase(str(tmp_path))


def test_product_line_match(sample_kb):
    result = sample_kb.lookup("Dell Inc", "PowerEdge R740", "")
    assert result == ("server", "physical_server", 0.90)


def test_manufacturer_alias_normalization(sample_kb):
    result = sample_kb.lookup("Hewlett Packard Enterprise", "ProLiant DL380", "")
    assert result == ("server", "physical_server", 0.90)


def test_single_category_manufacturer(sample_kb):
    """Fortinet only appears in one category — manufacturer-only match should work."""
    result = sample_kb.lookup("Fortinet", "FortiGate 60F", "")
    assert result == ("network", "firewall", 0.85)


def test_ambiguous_manufacturer_skipped(sample_kb):
    """Dell has product lines in server + workstation — ambiguous, should not
    fall back to manufacturer-only match."""
    result = sample_kb.lookup("Dell", "Unknown Model XYZ", "")
    assert result is None  # Dell is in server + workstation categories


def test_oui_fallback(sample_kb):
    """When manufacturer/model don't match, try OUI via mac_vendor."""
    result = sample_kb.lookup("", "", "VMware, Inc.")
    assert result is not None
    assert result[0] == "server"


def test_no_match_returns_none(sample_kb):
    result = sample_kb.lookup("Acme Corp", "Widget 3000", "")
    assert result is None


def test_case_insensitive(sample_kb):
    """Lookup should be case-insensitive for both manufacturer and model."""
    result = sample_kb.lookup("FORTINET", "fortigate 100f", "")
    assert result == ("network", "firewall", 0.85)


def test_empty_data_dir():
    """Non-existent data directory should not crash — just return no matches."""
    kb = DeviceKnowledgeBase("/nonexistent/path")
    assert kb.lookup("Dell", "Latitude 7490", "") is None


def test_missing_csv_files(tmp_path):
    """Only manufacturers.csv present — missing CSVs should not crash."""
    (tmp_path / "manufacturers.csv").write_text(
        "manufacturer_normalized,category,subcategory_default,confidence,source\n"
        "fortinet,network,firewall,0.85,nmap\n"
    )
    kb = DeviceKnowledgeBase(str(tmp_path))
    result = kb.lookup("Fortinet", "FortiGate 60F", "")
    assert result == ("network", "firewall", 0.85)


def test_hp_ambiguous_with_product_lines(sample_kb):
    """HP has server + workstation + printer product lines — ambiguous.
    Without a product line match, manufacturer-only should be skipped."""
    result = sample_kb.lookup("HP", "Unknown Device", "")
    assert result is None


def test_oui_ambiguous_manufacturer_skipped(sample_kb):
    """OUI fallback should also skip ambiguous manufacturers."""
    # Canon is only in one category (printer), so OUI should work for it
    result = sample_kb.lookup("", "", "Canon Inc.")
    assert result is not None
    assert result[0] == "printer"


def test_whitespace_handling(sample_kb):
    """Leading/trailing whitespace should be stripped."""
    result = sample_kb.lookup("  Dell Inc  ", "  PowerEdge R740  ", "")
    assert result == ("server", "physical_server", 0.90)


class TestIsKnownModel:
    """Test model-name detection for hostname validation."""

    def test_known_model_mp56(self, sample_kb):
        """Poly model 'mp56-e2' should be recognized."""
        assert sample_kb.is_known_model('mp56-e2') is True

    def test_known_model_vvx(self, sample_kb):
        """VoIP model 'vvx500' should be recognized."""
        assert sample_kb.is_known_model('vvx500') is True

    def test_known_model_elitebook(self, sample_kb):
        """Short model name 'elitebook' should be recognized."""
        assert sample_kb.is_known_model('elitebook') is True

    def test_known_model_poweredge(self, sample_kb):
        """Model 'poweredge' should be recognized."""
        assert sample_kb.is_known_model('poweredge') is True

    def test_unknown_hostname(self, sample_kb):
        """Normal hostname should not be recognized as model."""
        assert sample_kb.is_known_model('DECL76JLZ') is False

    def test_long_hostname_not_matched(self, sample_kb):
        """Long hostname starting with model prefix should not match."""
        assert sample_kb.is_known_model('enterprise-server-01') is False

    def test_case_insensitive(self, sample_kb):
        """Model matching should be case-insensitive."""
        assert sample_kb.is_known_model('EliteBook') is True

    def test_empty_string(self, sample_kb):
        """Empty string should not match."""
        assert sample_kb.is_known_model('') is False
