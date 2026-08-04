# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Task 6 (category_mapping) and Task 7 (nmap_os_classes scraper).

These tests are unit-level — no network calls are made.
"""
import sys
sys.path.insert(0, '.')

import pytest

from data_scrapers.device_knowledge.category_mapping import map_device_type
from data_scrapers.device_knowledge.scrapers.nmap_os_classes import (
    parse_os_classes,
    _normalize_vendor,
)


# ---------------------------------------------------------------------------
# Task 6: category_mapping.map_device_type
# ---------------------------------------------------------------------------

class TestNmapMappings:
    """All 26 NMAP device types produce the correct canonical result."""

    @pytest.mark.parametrize("device_type, expected", [
        ("general purpose", ("workstation", "unknown")),
        ("router", ("network", "router")),
        ("switch", ("network", "switch")),
        ("firewall", ("network", "firewall")),
        ("bridge", ("network", "bridge")),
        ("broadband router", ("network", "router")),
        ("WAP", ("network", "wireless_access_point")),
        ("printer", ("printer", "unknown")),
        ("print server", ("printer", "print_server")),
        ("terminal", ("workstation", "thin_client")),
        ("terminal server", ("server", "unknown")),
        ("remote management", ("server", "unknown")),
        ("telecom-misc", ("network", "unknown")),
        ("VoIP adapter", ("network", "voip")),
        ("VoIP phone", ("mobile", "phone")),
        ("PBX", ("network", "pbx")),
        ("power-device", ("ot", "unknown")),
        ("storage-misc", ("storage", "unknown")),
        ("media device", ("iot", "unknown")),
        ("webcam", ("iot", "unknown")),
        ("load balancer", ("network", "load_balancer")),
        ("proxy server", ("network", "unknown")),
        ("game console", ("iot", "unknown")),
        ("hub", ("network", "hub")),
        ("phone", ("mobile", "phone")),
    ])
    def test_nmap_device_type(self, device_type, expected):
        result = map_device_type(device_type, source="nmap")
        assert result == expected, (
            f"map_device_type({device_type!r}, 'nmap'): expected {expected}, got {result}"
        )

    def test_specialized_returns_none(self):
        """'specialized' is too generic — must return None."""
        assert map_device_type("specialized", source="nmap") is None

    def test_case_insensitive(self):
        assert map_device_type("ROUTER", source="nmap") == ("network", "router")
        assert map_device_type("General Purpose", source="nmap") == ("workstation", "unknown")
        assert map_device_type("FIREWALL", source="nmap") == ("network", "firewall")

    def test_unknown_type_returns_none(self):
        assert map_device_type("unknown-gadget", source="nmap") is None
        assert map_device_type("", source="nmap") is None

    def test_leading_trailing_whitespace_stripped(self):
        assert map_device_type("  router  ", source="nmap") == ("network", "router")


class TestNetboxMappings:
    """Key NetBox device types map correctly; infrastructure types return None."""

    @pytest.mark.parametrize("device_type, expected", [
        ("server", ("server", "unknown")),
        ("virtual machine", ("server", "vm")),
        ("router", ("network", "router")),
        ("switch", ("network", "switch")),
        ("firewall", ("network", "firewall")),
        ("nas", ("storage", "nas")),
        ("san", ("storage", "san")),
        ("printer", ("printer", "unknown")),
        ("workstation", ("workstation", "unknown")),
        ("laptop", ("workstation", "laptop")),
        ("phone", ("mobile", "phone")),
        ("tablet", ("mobile", "tablet")),
        ("ip camera", ("iot", "security_camera")),
        ("plc", ("ot", "plc")),
        ("scada", ("ot", "scada")),
    ])
    def test_netbox_device_type(self, device_type, expected):
        result = map_device_type(device_type, source="netbox")
        assert result == expected

    @pytest.mark.parametrize("infrastructure_type", [
        "patch panel",
        "cable management",
        "pdu",
        "kvm switch",
        "rack",
    ])
    def test_infrastructure_returns_none(self, infrastructure_type):
        assert map_device_type(infrastructure_type, source="netbox") is None

    def test_unknown_netbox_type_returns_none(self):
        assert map_device_type("something-weird", source="netbox") is None


class TestFingerbankMappings:
    """Key Fingerbank device types map correctly."""

    @pytest.mark.parametrize("device_type, expected", [
        ("Programmable Logic Controller (PLC)", ("ot", "plc")),
        ("Switch/Router", ("network", "switch")),
        ("router", ("network", "router")),
        ("VoIP phone", ("mobile", "phone")),
        ("IP camera", ("iot", "security_camera")),
        ("NAS", ("storage", "nas")),
        ("HVAC", ("ot", "hvac")),
        ("multifunction printer", ("printer", "mfp")),
        ("smartphone", ("mobile", "phone")),
        ("game console", ("iot", "unknown")),
    ])
    def test_fingerbank_device_type(self, device_type, expected):
        result = map_device_type(device_type, source="fingerbank")
        assert result == expected

    def test_unknown_fingerbank_type_returns_none(self):
        assert map_device_type("niche-gadget", source="fingerbank") is None


class TestUnknownSource:
    """Passing an unrecognized source always returns None."""

    def test_unknown_source(self):
        assert map_device_type("router", source="made_up_source") is None


# ---------------------------------------------------------------------------
# Task 7: nmap_os_classes scraper — parse_os_classes (no network)
# ---------------------------------------------------------------------------

_SAMPLE_LINES = [
    "# Comment line — should be skipped",
    "",
    "3Com embedded broadband router",
    "Cisco IOS 12.X router",
    "Linux 2.6.X general purpose",
    "Axis camera specialized",          # specialized -> None (skipped)
    "HP LaserJet printer",
    "Cisco Catalyst switch",
    "Linux 2.4.X firewall",
    "Netgear WPN824 WAP",
    "Polycom VoIP phone",
    "Zebra ZPL printer",
    "APC power-device",
    "Sony game console",
    "Axis webcam",
    "Cisco IOS 11.X terminal server",
    "HP JetDirect print server",
    "Meridian PBX",
    "Nortel telecom-misc",
    "Citrix load balancer",
    "Squid proxy server",
]


class TestParseOsClasses:
    """parse_os_classes correctly parses sample lines."""

    def test_skips_comments_and_blanks(self):
        records = parse_os_classes(_SAMPLE_LINES)
        sources = [r["source"] for r in records]
        assert all(s == "nmap" for s in sources)
        # No record should come from the comment or blank line
        manufacturers = [r["manufacturer"] for r in records]
        assert "#" not in " ".join(manufacturers)

    def test_specialized_is_excluded(self):
        records = parse_os_classes(_SAMPLE_LINES)
        device_types = [r["device_type"] for r in records]
        assert "specialized" not in device_types

    def test_broadband_router_parsed(self):
        records = parse_os_classes(_SAMPLE_LINES)
        matches = [r for r in records if r["device_type"] == "broadband router"]
        assert len(matches) == 1
        r = matches[0]
        assert r["manufacturer"] == "3com"
        assert r["category"] == "network"
        assert r["subcategory"] == "router"
        assert r["source"] == "nmap"

    def test_cisco_router_with_os(self):
        records = parse_os_classes(_SAMPLE_LINES)
        matches = [r for r in records if r["device_type"] == "router" and "cisco" in r["manufacturer"]]
        assert len(matches) == 1
        r = matches[0]
        assert r["manufacturer"] == "cisco"
        assert r["os"] == "IOS 12.X"
        assert r["category"] == "network"
        assert r["subcategory"] == "router"

    def test_linux_general_purpose(self):
        records = parse_os_classes(_SAMPLE_LINES)
        matches = [r for r in records if r["device_type"] == "general purpose"]
        assert len(matches) == 1
        r = matches[0]
        assert r["manufacturer"] == "linux"
        assert r["os"] == "2.6.X"
        assert r["category"] == "workstation"
        assert r["subcategory"] == "unknown"

    def test_printer(self):
        records = parse_os_classes(_SAMPLE_LINES)
        printer_records = [r for r in records if r["device_type"] == "printer"]
        assert len(printer_records) >= 1
        for r in printer_records:
            assert r["category"] == "printer"

    def test_voip_phone(self):
        records = parse_os_classes(_SAMPLE_LINES)
        matches = [r for r in records if r["device_type"] == "voip phone"]
        assert len(matches) == 1
        assert matches[0]["category"] == "mobile"
        assert matches[0]["subcategory"] == "phone"

    def test_power_device(self):
        records = parse_os_classes(_SAMPLE_LINES)
        matches = [r for r in records if r["device_type"] == "power-device"]
        assert len(matches) == 1
        assert matches[0]["category"] == "ot"

    def test_game_console(self):
        records = parse_os_classes(_SAMPLE_LINES)
        matches = [r for r in records if r["device_type"] == "game console"]
        assert len(matches) == 1
        assert matches[0]["category"] == "iot"

    def test_all_records_have_required_keys(self):
        records = parse_os_classes(_SAMPLE_LINES)
        required_keys = {"manufacturer", "os", "device_type", "category", "subcategory", "source"}
        for r in records:
            assert required_keys == set(r.keys()), f"Missing keys in record: {r}"

    def test_empty_input(self):
        assert parse_os_classes([]) == []

    def test_only_comments(self):
        assert parse_os_classes(["# comment", "# another"]) == []


class TestNormalizeVendor:
    """_normalize_vendor strips legal suffixes and lowercases."""

    @pytest.mark.parametrize("raw, expected", [
        ("Cisco", "cisco"),
        ("3Com", "3com"),
        ("HP", "hp"),
        ("Microsoft Corporation", "microsoft"),
        ("Dell Inc.", "dell"),
        ("Juniper Networks", "juniper"),
        ("Palo Alto Networks", "palo alto"),
        ("F5 Networks", "f5"),
        ("NetGear", "netgear"),
        ("Apple Inc", "apple"),
    ])
    def test_vendor_normalization(self, raw, expected):
        result = _normalize_vendor(raw)
        assert result == expected, f"_normalize_vendor({raw!r}): expected {expected!r}, got {result!r}"


# ---------------------------------------------------------------------------
# Task 8: new scraper parse functions — no network calls
# ---------------------------------------------------------------------------

import re as _re

from data_scrapers.device_knowledge.scrapers.netbox_devicetypes import parse_device_yaml
from data_scrapers.device_knowledge.scrapers.cpe_dictionary import parse_cpe_lines
from data_scrapers.device_knowledge.scrapers.fingerbank_devices import parse_devices
from data_scrapers.device_knowledge.scrapers.ieee_oui import parse_oui_csv
from data_scrapers.device_knowledge.scrapers.fusioninventory_snmp import parse_sysobject_ids
from data_scrapers.device_knowledge.scrapers.hwdata_vendors import parse_ids_file


# --- NetBox ---

_NETBOX_ROUTER_YAML = """\
manufacturer: Cisco
model: ASR 1001-X
slug: cisco-asr-1001-x
u_height: 1
comments: Enterprise router chassis
"""

_NETBOX_SWITCH_YAML = """\
manufacturer: Aruba
model: 2930F-24G Switch
slug: aruba-2930f-24g-switch
u_height: 1
"""

_NETBOX_INFRA_YAML = """\
manufacturer: Panduit
model: NetRunner-48
slug: panduit-netrunner-48
comments: patch panel for structured cabling
"""

_NETBOX_SERVER_YAML = """\
manufacturer: Dell
model: PowerEdge R740
slug: dell-poweredge-r740
u_height: 2
comments: 2U rack server
"""


class TestNetboxParseDeviceYaml:
    """parse_device_yaml correctly extracts and categorises device records."""

    def test_router_yaml(self):
        record = parse_device_yaml("Cisco", _NETBOX_ROUTER_YAML, filename="cisco-asr-1001-x")
        assert record is not None
        assert record["manufacturer"] == "Cisco"
        assert record["category"] == "network"
        assert record["device_type"] == "router"
        assert record["source"] == "netbox"

    def test_switch_yaml(self):
        record = parse_device_yaml("Aruba", _NETBOX_SWITCH_YAML, filename="aruba-2930f-24g-switch")
        assert record is not None
        assert record["category"] == "network"
        assert record["device_type"] == "switch"

    def test_infrastructure_returns_none(self):
        """Patch panels map to None and should be excluded."""
        record = parse_device_yaml("Panduit", _NETBOX_INFRA_YAML, filename="panduit-netrunner-48")
        assert record is None

    def test_server_yaml(self):
        record = parse_device_yaml("Dell", _NETBOX_SERVER_YAML, filename="dell-poweredge-r740")
        assert record is not None
        assert record["category"] == "server"
        assert record["subcategory"] == "unknown"

    def test_record_keys(self):
        record = parse_device_yaml("Cisco", _NETBOX_ROUTER_YAML, filename="cisco-asr-1001-x")
        assert record is not None
        assert set(record.keys()) == {
            "manufacturer", "model", "slug", "device_type", "category", "subcategory", "source"
        }

    def test_unrecognised_yaml_returns_none(self):
        yaml_text = "manufacturer: Acme\nmodel: Widget-9000\nslug: acme-widget-9000\n"
        assert parse_device_yaml("Acme", yaml_text, filename="acme-widget-9000") is None


# --- CPE dictionary ---

_CPE_LINES = [
    "# comment line",
    "",
    "cpe:2.3:h:cisco:asr_1001-x:*:*:*:*:*:*:*:*",
    "cpe:2.3:h:juniper:srx300:*:*:*:*:*:*:*:*",
    "cpe:2.3:o:linux:linux_kernel:5.15:*:*:*:*:*:*:*",   # OS — should be skipped
    "cpe:2.3:a:apache:http_server:2.4:*:*:*:*:*:*:*",    # App — should be skipped
    "cpe:2.3:h:hp:laserjet_pro_m402:*:*:*:*:*:*:*:*",
    "cisco\thp_laserjet_pro",                              # tab format
]


class TestParseCpeLines:
    """parse_cpe_lines filters hardware entries and normalises names."""

    def test_hardware_entries_returned(self):
        records = parse_cpe_lines(_CPE_LINES)
        vendors = [r["manufacturer"] for r in records]
        assert "cisco" in vendors
        assert "juniper" in vendors

    def test_os_and_app_entries_excluded(self):
        records = parse_cpe_lines(_CPE_LINES)
        vendors = [r["manufacturer"] for r in records]
        assert "linux" not in vendors
        assert "apache" not in vendors

    def test_record_keys(self):
        records = parse_cpe_lines(_CPE_LINES)
        assert records
        for r in records:
            assert set(r.keys()) == {"manufacturer", "model", "source"}

    def test_source_field(self):
        records = parse_cpe_lines(_CPE_LINES)
        assert all(r["source"] == "cpe" for r in records)

    def test_comments_and_blanks_skipped(self):
        records = parse_cpe_lines(["# comment", "", "not-a-cpe-line"])
        assert records == []

    def test_deduplication(self):
        duped = [
            "cpe:2.3:h:cisco:asr_1001-x:*:*:*:*:*:*:*:*",
            "cpe:2.3:h:cisco:asr_1001-x:*:*:*:*:*:*:*:*",
        ]
        records = parse_cpe_lines(duped)
        assert len(records) == 1


# --- Fingerbank ---

_FINGERBANK_DEVICES = [
    {"id": 1, "name": "Router", "parent": None},
    {"id": 2, "name": "Switch", "parent": None},
    {"id": 3, "name": "NAS", "parent": None},
    {"id": 4, "name": "Printer", "parent": None},
    {"id": 5, "name": "niche-gadget-xyz", "parent": None},   # unmappable
    {"id": 6, "name": "Laptop", "parent": None},
]


class TestParseFingerbankDevices:
    """parse_devices maps known device names to canonical categories."""

    def test_router_mapped(self):
        records = parse_devices(_FINGERBANK_DEVICES)
        matches = [r for r in records if r["device_type"] == "router"]
        assert len(matches) == 1
        assert matches[0]["category"] == "network"

    def test_nas_mapped(self):
        records = parse_devices(_FINGERBANK_DEVICES)
        matches = [r for r in records if r["device_type"] == "nas"]
        assert len(matches) == 1
        assert matches[0]["category"] == "storage"

    def test_unmappable_excluded(self):
        records = parse_devices(_FINGERBANK_DEVICES)
        types = [r["device_type"] for r in records]
        assert "niche-gadget-xyz" not in types

    def test_record_keys(self):
        records = parse_devices(_FINGERBANK_DEVICES)
        assert records
        for r in records:
            assert set(r.keys()) == {
                "manufacturer", "device_type", "category", "subcategory",
                "fingerbank_id", "source",
            }

    def test_source_field(self):
        records = parse_devices(_FINGERBANK_DEVICES)
        assert all(r["source"] == "fingerbank" for r in records)

    def test_empty_input(self):
        assert parse_devices([]) == []


# --- IEEE OUI ---

_OUI_CSV_LINES = [
    "macPrefix,vendorName,private,blockType,lastUpdate",
    "AA:BB:CC,Cisco Systems Inc,False,MA-L,2023-01-01",
    "11:22:33,Juniper Networks,False,MA-L,2023-01-01",
    "FF:FF:FF,Private,True,MA-L,2023-01-01",           # private — skipped
    "# comment line",
    "",
    "DD:EE:FF,Hewlett Packard Enterprise,False,MA-L,2023-01-01",
    "AA:BB:CC,Duplicate Cisco,False,MA-L,2023-01-01",  # duplicate prefix — skipped
]


class TestParseOuiCsv:
    """parse_oui_csv extracts and normalises MAC prefix / vendor pairs."""

    def test_vendors_extracted(self):
        records = parse_oui_csv(_OUI_CSV_LINES)
        vendors = [r["manufacturer"] for r in records]
        assert any("cisco" in v for v in vendors)
        assert any("juniper" in v for v in vendors)

    def test_private_entries_skipped(self):
        records = parse_oui_csv(_OUI_CSV_LINES)
        prefixes = [r["mac_prefix"] for r in records]
        assert "FF:FF:FF" not in prefixes

    def test_header_skipped(self):
        records = parse_oui_csv(_OUI_CSV_LINES)
        vendors = [r["manufacturer"] for r in records]
        assert "macprefix" not in vendors

    def test_deduplication(self):
        records = parse_oui_csv(_OUI_CSV_LINES)
        prefixes = [r["mac_prefix"] for r in records]
        assert len(prefixes) == len(set(prefixes))

    def test_mac_prefix_format(self):
        records = parse_oui_csv(_OUI_CSV_LINES)
        for r in records:
            assert _re.match(r"^[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}$", r["mac_prefix"]), (
                f"Invalid MAC prefix format: {r['mac_prefix']}"
            )

    def test_record_keys(self):
        records = parse_oui_csv(_OUI_CSV_LINES)
        assert records
        for r in records:
            assert set(r.keys()) == {"mac_prefix", "manufacturer", "source"}

    def test_source_field(self):
        records = parse_oui_csv(_OUI_CSV_LINES)
        assert all(r["source"] == "ieee_oui" for r in records)

    def test_empty_input(self):
        assert parse_oui_csv([]) == []


# --- FusionInventory SNMP ---

_SYSOBJECT_LINES = [
    "# FusionInventory sysobject.ids",
    "",
    ".1.3.6.1.4.1.9.1.1|Cisco|Networking|Cisco Catalyst 2950",
    ".1.3.6.1.4.1.2636.1.1.1.2.34|Juniper|Router|MX80",
    ".1.3.6.1.4.1.11.2.3.9.1|HP|Printer|LaserJet 4050",
    ".1.3.6.1.4.1.232.2.2.4.2|Compaq|Server|ProLiant DL380",
    ".1.3.6.1.4.1.999.1.1|Unknown|widget|",  # unmappable type
]


class TestParseSysobjectIds:
    """parse_sysobject_ids maps FusionInventory device types correctly."""

    def test_networking_mapped(self):
        records = parse_sysobject_ids(_SYSOBJECT_LINES)
        matches = [r for r in records if "cisco" in r["manufacturer"]]
        assert len(matches) == 1
        assert matches[0]["category"] == "network"

    def test_router_mapped(self):
        records = parse_sysobject_ids(_SYSOBJECT_LINES)
        matches = [r for r in records if "juniper" in r["manufacturer"]]
        assert len(matches) == 1
        assert matches[0]["category"] == "network"

    def test_printer_mapped(self):
        records = parse_sysobject_ids(_SYSOBJECT_LINES)
        matches = [r for r in records if "hp" in r["manufacturer"]]
        assert len(matches) == 1
        assert matches[0]["category"] == "printer"

    def test_server_mapped(self):
        records = parse_sysobject_ids(_SYSOBJECT_LINES)
        matches = [r for r in records if "compaq" in r["manufacturer"]]
        assert len(matches) == 1
        assert matches[0]["category"] == "server"

    def test_unmappable_type_excluded(self):
        records = parse_sysobject_ids(_SYSOBJECT_LINES)
        types = [r["device_type"] for r in records]
        assert "widget" not in types

    def test_comments_and_blanks_skipped(self):
        records = parse_sysobject_ids(["# comment", ""])
        assert records == []

    def test_record_keys(self):
        records = parse_sysobject_ids(_SYSOBJECT_LINES)
        assert records
        for r in records:
            assert set(r.keys()) == {
                "oid", "manufacturer", "device_type", "category", "subcategory", "source"
            }

    def test_source_field(self):
        records = parse_sysobject_ids(_SYSOBJECT_LINES)
        assert all(r["source"] == "fusioninventory" for r in records)


# --- hwdata vendors ---

_PCI_IDS_LINES = [
    "# List of PCI ID's",
    "# Syntax:",
    "",
    "1000  Broadcom / LSI",
    "  0001  53c810",             # device line — skipped
    "    1000 1000  DAC960P RAID", # subsystem line — skipped
    "8086  Intel Corporation",
    "  1533  I210 Gigabit Network Connection",
    "10de  NVIDIA Corporation",
    "14e4  Broadcom Inc. and subsidiaries",
]

_USB_IDS_LINES = [
    "# List of USB ID's",
    "",
    "04b4  Cypress Semiconductor Corp.",
    "  0001  Mouse",              # device line — skipped
    "046d  Logitech, Inc.",
    "045e  Microsoft Corp.",
]


class TestParseIdsFile:
    """parse_ids_file extracts only vendor lines from .ids files."""

    def test_pci_vendors_extracted(self):
        records = parse_ids_file(_PCI_IDS_LINES, id_type="pci")
        vendors = [r["manufacturer"] for r in records]
        assert any("broadcom" in v for v in vendors)
        assert any("intel" in v for v in vendors)
        assert any("nvidia" in v for v in vendors)

    def test_device_lines_skipped(self):
        records = parse_ids_file(_PCI_IDS_LINES, id_type="pci")
        # Device lines start with whitespace — vendor count should be 4
        assert len(records) == 4

    def test_usb_vendors_extracted(self):
        records = parse_ids_file(_USB_IDS_LINES, id_type="usb")
        vendors = [r["manufacturer"] for r in records]
        assert any("logitech" in v for v in vendors)
        assert any("microsoft" in v for v in vendors)

    def test_usb_device_lines_skipped(self):
        records = parse_ids_file(_USB_IDS_LINES, id_type="usb")
        assert len(records) == 3

    def test_id_type_field(self):
        pci_records = parse_ids_file(_PCI_IDS_LINES, id_type="pci")
        usb_records = parse_ids_file(_USB_IDS_LINES, id_type="usb")
        assert all(r["id_type"] == "pci" for r in pci_records)
        assert all(r["id_type"] == "usb" for r in usb_records)

    def test_record_keys(self):
        records = parse_ids_file(_PCI_IDS_LINES, id_type="pci")
        assert records
        for r in records:
            assert set(r.keys()) == {"vendor_id", "manufacturer", "id_type", "source"}

    def test_source_field(self):
        records = parse_ids_file(_PCI_IDS_LINES, id_type="pci")
        assert all(r["source"] == "hwdata" for r in records)

    def test_deduplication(self):
        duped = [
            "1000  Broadcom / LSI",
            "1000  Broadcom duplicate",
        ]
        records = parse_ids_file(duped, id_type="pci")
        assert len(records) == 1

    def test_empty_input(self):
        assert parse_ids_file([], id_type="pci") == []

    def test_vendor_id_format(self):
        records = parse_ids_file(_PCI_IDS_LINES, id_type="pci")
        for r in records:
            assert _re.match(r"^[0-9a-f]{4}$", r["vendor_id"]), (
                f"Invalid vendor_id format: {r['vendor_id']}"
            )


# --- __init__.py ALL_SCRAPERS registry ---

from data_scrapers.device_knowledge.scrapers import ALL_SCRAPERS


class TestAllScrapersRegistry:
    """ALL_SCRAPERS dict contains all 8 expected keys."""

    def test_all_keys_present(self):
        expected = {"nmap", "netbox", "cpe", "fingerbank", "ieee_oui", "fusioninventory", "hwdata", "recog"}
        assert set(ALL_SCRAPERS.keys()) == expected

    def test_all_values_callable(self):
        for name, fn in ALL_SCRAPERS.items():
            assert callable(fn), f"ALL_SCRAPERS[{name!r}] is not callable"


# ---------------------------------------------------------------------------
# Task 9: normalize_and_write
# ---------------------------------------------------------------------------

import csv
import os
import tempfile

from data_scrapers.device_knowledge.normalize import normalize_and_write


def _make_record(**kwargs) -> dict:
    """Helper to build a scraper result dict with defaults."""
    defaults = {
        "manufacturer": "",
        "model": "",
        "category": "",
        "subcategory": "unknown",
        "confidence": 0.80,
        "source": "nmap",
        "device_type": "",
    }
    defaults.update(kwargs)
    return defaults


class TestNormalizeDeduplication:
    """Same manufacturer from 2 sources — keep higher-confidence entry."""

    def test_keep_higher_confidence(self):
        results = [
            _make_record(manufacturer="Cisco", category="network", subcategory="router",
                         confidence=0.70, source="nmap"),
            _make_record(manufacturer="Cisco", category="network", subcategory="switch",
                         confidence=0.90, source="netbox"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            normalize_and_write(results, tmpdir)
            with open(os.path.join(tmpdir, "manufacturers.csv"), newline="") as f:
                rows = list(_re.split(r"\r?\n", f.read().strip()))
            # Header + 1 data row
            assert len(rows) == 2
            # The kept entry must have confidence 0.90 (from netbox)
            assert "0.9" in rows[1] or "0.90" in rows[1]

    def test_same_confidence_source_priority(self):
        """Fingerbank beats nmap at equal confidence."""
        results = [
            _make_record(manufacturer="Juniper", category="network", subcategory="router",
                         confidence=0.80, source="nmap"),
            _make_record(manufacturer="Juniper", category="network", subcategory="router",
                         confidence=0.80, source="fingerbank"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            normalize_and_write(results, tmpdir)
            with open(os.path.join(tmpdir, "manufacturers.csv"), newline="") as f:
                content = f.read()
            assert "fingerbank" in content


class TestNormalizeAmbiguous:
    """Manufacturer with 2+ categories excluded from manufacturers.csv."""

    def test_ambiguous_excluded_from_manufacturers(self):
        # "hp" appears as both "server" and "printer" — should be ambiguous
        results = [
            _make_record(manufacturer="HP", category="server", confidence=0.80, source="netbox"),
            _make_record(manufacturer="HP", category="printer", confidence=0.85, source="netbox"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            normalize_and_write(results, tmpdir)
            with open(os.path.join(tmpdir, "manufacturers.csv"), newline="") as f:
                content = f.read()
            # "hp" should NOT appear in manufacturers.csv
            assert "hp" not in content.lower().split("\n")[1:]  # skip header

    def test_non_ambiguous_included(self):
        results = [
            _make_record(manufacturer="Cisco", category="network", confidence=0.85, source="nmap"),
            _make_record(manufacturer="Cisco", category="network", confidence=0.80, source="netbox"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            normalize_and_write(results, tmpdir)
            with open(os.path.join(tmpdir, "manufacturers.csv"), newline="") as f:
                content = f.read()
            assert "cisco" in content

    def test_ambiguous_product_lines_still_written(self):
        """Product lines for ambiguous manufacturers are kept in product_lines.csv."""
        results = [
            _make_record(manufacturer="HP", model="LaserJet Pro", category="printer",
                         confidence=0.85, source="netbox"),
            _make_record(manufacturer="HP", model="ProLiant DL380", category="server",
                         confidence=0.85, source="netbox"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            normalize_and_write(results, tmpdir)
            with open(os.path.join(tmpdir, "product_lines.csv"), newline="") as f:
                content = f.read()
            # Both product lines should be present
            assert "laserjet" in content.lower() or "^laserjet" in content.lower() or "^laser" in content.lower()
            assert "proliant" in content.lower() or "^proliant" in content.lower()


class TestNormalizeCsvFormat:
    """Output CSV files have the expected columns and are valid CSV."""

    def _run(self, results):
        tmpdir = tempfile.mkdtemp()
        normalize_and_write(results, tmpdir)
        return tmpdir

    def test_manufacturers_csv_columns(self):
        results = [
            _make_record(manufacturer="Dell", category="server", confidence=0.85, source="netbox"),
        ]
        tmpdir = self._run(results)
        with open(os.path.join(tmpdir, "manufacturers.csv"), newline="") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {
                "manufacturer_normalized", "category", "subcategory_default",
                "confidence", "source"
            }

    def test_product_lines_csv_columns(self):
        results = [
            _make_record(manufacturer="Dell", model="PowerEdge R740", category="server",
                         confidence=0.90, source="netbox"),
        ]
        tmpdir = self._run(results)
        with open(os.path.join(tmpdir, "product_lines.csv"), newline="") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {
                "manufacturer_normalized", "model_pattern", "category",
                "subcategory", "confidence", "source"
            }

    def test_oui_categories_csv_columns(self):
        results = [
            {"mac_prefix": "AA:BB:CC", "manufacturer": "Cisco", "source": "ieee_oui"},
            _make_record(manufacturer="Cisco", category="network", confidence=0.85, source="netbox"),
        ]
        tmpdir = self._run(results)
        with open(os.path.join(tmpdir, "oui_categories.csv"), newline="") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {
                "oui_prefix", "manufacturer_normalized", "category", "source"
            }

    def test_manufacturer_aliases_csv_columns(self):
        results = [
            {"vendor_id": "1234", "manufacturer": "Cisco Systems", "id_type": "pci", "source": "hwdata"},
        ]
        tmpdir = self._run(results)
        with open(os.path.join(tmpdir, "manufacturer_aliases.csv"), newline="") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {
                "manufacturer_normalized", "alias_pattern", "raw_names"
            }

    def test_empty_input_writes_headers_only(self):
        tmpdir = self._run([])
        for filename in ("manufacturers.csv", "product_lines.csv",
                         "oui_categories.csv", "manufacturer_aliases.csv"):
            path = os.path.join(tmpdir, filename)
            assert os.path.isfile(path), f"Missing: {filename}"
            with open(path, newline="") as f:
                lines = [l for l in f.read().strip().split("\n") if l]
            # Only header row, no data
            assert len(lines) == 1, f"{filename} should have only header row"

    def test_model_pattern_format(self):
        """model_pattern in product_lines.csv should start with '^'."""
        results = [
            _make_record(manufacturer="Dell", model="PowerEdge R740", category="server",
                         confidence=0.90, source="netbox"),
        ]
        tmpdir = self._run(results)
        with open(os.path.join(tmpdir, "product_lines.csv"), newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert rows, "product_lines.csv has no data rows"
        for row in rows:
            assert row["model_pattern"].startswith("^"), (
                f"model_pattern should start with '^': {row['model_pattern']!r}"
            )


# ---------------------------------------------------------------------------
# Task 10: Rapid7 Recog scraper — parse_recog_xml (no network)
# ---------------------------------------------------------------------------

from data_scrapers.device_knowledge.scrapers.recog_fingerprints import parse_recog_xml

SAMPLE_RECOG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fingerprints matches="snmp.sys_description">
  <fingerprint pattern="^Cisco IOS.*">
    <description>Cisco IOS Router</description>
    <example>Cisco IOS Software, C2900 Software</example>
    <param pos="0" name="os.vendor" value="Cisco"/>
    <param pos="0" name="os.product" value="IOS"/>
    <param pos="0" name="os.device" value="Router"/>
    <param pos="1" name="os.version"/>
  </fingerprint>
  <fingerprint pattern="^HP ETHERNET MULTI-ENVIRONMENT.*">
    <description>HP Printer</description>
    <example>HP ETHERNET MULTI-ENVIRONMENT</example>
    <param pos="0" name="hw.vendor" value="HP"/>
    <param pos="0" name="hw.product" value="Printer"/>
    <param pos="0" name="hw.device" value="Printer"/>
  </fingerprint>
  <fingerprint pattern="^SomeApp.*">
    <description>Generic software</description>
    <example>SomeApp v1.0</example>
    <param pos="0" name="service.vendor" value="SomeCorp"/>
    <param pos="0" name="service.product" value="SomeApp"/>
  </fingerprint>
</fingerprints>"""

# XML with dynamic-only params (no static value on device param)
_DYNAMIC_ONLY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fingerprints matches="snmp.sys_description">
  <fingerprint pattern="^(.+) Router.*">
    <param pos="1" name="os.vendor"/>
    <param pos="1" name="os.device"/>
  </fingerprint>
</fingerprints>"""

# XML with two fingerprints that share the same (vendor, product, device_type)
_DUPLICATE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fingerprints matches="http.server">
  <fingerprint pattern="^Cisco.*v1.*">
    <param pos="0" name="os.vendor" value="Cisco"/>
    <param pos="0" name="os.product" value="IOS"/>
    <param pos="0" name="os.device" value="Router"/>
  </fingerprint>
  <fingerprint pattern="^Cisco.*v2.*">
    <param pos="0" name="os.vendor" value="Cisco"/>
    <param pos="0" name="os.product" value="IOS"/>
    <param pos="0" name="os.device" value="Router"/>
  </fingerprint>
</fingerprints>"""


class TestParseRecogXml:
    """parse_recog_xml correctly extracts and categorises fingerprint records."""

    def test_cisco_router_extracted(self):
        records = parse_recog_xml(SAMPLE_RECOG_XML)
        matches = [r for r in records if r["device_type"] == "Router"]
        assert len(matches) == 1
        r = matches[0]
        assert r["manufacturer"] == "Cisco"
        assert r["model"] == "IOS"
        assert r["category"] == "network"
        assert r["subcategory"] == "router"
        assert r["source"] == "recog"

    def test_hp_printer_extracted(self):
        records = parse_recog_xml(SAMPLE_RECOG_XML)
        matches = [r for r in records if r["device_type"] == "Printer"]
        assert len(matches) == 1
        r = matches[0]
        assert r["manufacturer"] == "HP"
        assert r["category"] == "printer"

    def test_no_device_type_skipped(self):
        """Third fingerprint has no hw/os.device param — must be excluded."""
        records = parse_recog_xml(SAMPLE_RECOG_XML)
        manufacturers = [r["manufacturer"] for r in records]
        assert "SomeCorp" not in manufacturers

    def test_dynamic_params_skipped(self):
        """Params with pos != '0' carry no static value — must be excluded."""
        records = parse_recog_xml(_DYNAMIC_ONLY_XML)
        assert records == []

    def test_deduplication_within_xml(self):
        """Two fingerprints with identical (vendor, product, device_type) yield one record."""
        records = parse_recog_xml(_DUPLICATE_XML)
        assert len(records) == 1

    def test_all_records_have_required_keys(self):
        records = parse_recog_xml(SAMPLE_RECOG_XML)
        required = {"manufacturer", "model", "device_type", "category", "subcategory", "source"}
        for r in records:
            assert set(r.keys()) == required, f"Missing keys in record: {r}"

    def test_source_field(self):
        records = parse_recog_xml(SAMPLE_RECOG_XML)
        assert all(r["source"] == "recog" for r in records)

    def test_empty_xml_returns_empty(self):
        records = parse_recog_xml('<?xml version="1.0"?><fingerprints/>')
        assert records == []

    def test_invalid_xml_returns_empty(self):
        records = parse_recog_xml("this is not xml <<>>")
        assert records == []


class TestRecogCategoryMappings:
    """Recog device types map to the correct canonical categories."""

    @pytest.mark.parametrize("device_type, expected", [
        ("Router", ("network", "router")),
        ("Switch", ("network", "switch")),
        ("Firewall", ("network", "firewall")),
        ("WAP", ("network", "wireless_access_point")),
        ("Printer", ("printer", "unknown")),
        ("Multifunction Device", ("printer", "unknown")),
        ("Print server", ("printer", "print_server")),
        ("VoIP", ("network", "voip")),
        ("VoIP Phone", ("mobile", "phone")),
        ("IP Camera", ("iot", "unknown")),
        ("Media Gateway", ("network", "unknown")),
        ("Web Proxy", ("network", "unknown")),
        ("Load Balancer", ("network", "load_balancer")),
        ("Thin Client", ("workstation", "thin_client")),
        ("KVM", ("server", "unknown")),
        ("UPS", ("ot", "unknown")),
        ("PLC", ("ot", "plc")),
        ("DSLAM", ("network", "unknown")),
        ("General Purpose", ("workstation", "unknown")),
        ("Server", ("server", "unknown")),
    ])
    def test_recog_device_type(self, device_type, expected):
        result = map_device_type(device_type, source="recog")
        assert result == expected, (
            f"map_device_type({device_type!r}, 'recog'): expected {expected}, got {result}"
        )

    def test_unknown_recog_type_returns_none(self):
        assert map_device_type("some-unknown-gadget", source="recog") is None

    def test_case_insensitive(self):
        assert map_device_type("ROUTER", source="recog") == ("network", "router")
        assert map_device_type("printer", source="recog") == ("printer", "unknown")
