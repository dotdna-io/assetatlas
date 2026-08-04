# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Category mapping module for the device knowledge scraping pipeline.

Translates external device type vocabularies into our canonical taxonomy:
  category: server | workstation | network | mobile | printer | storage | iot | ot | miscellaneous
  subcategory: free-form string (lowercase, underscores)

Usage:
    from data_scrapers.device_knowledge.category_mapping import map_device_type

    result = map_device_type("router", source="nmap")
    # -> ("network", "router")

    result = map_device_type("unknown-thing", source="nmap")
    # -> None
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# NMAP os-classes.txt device types
# Source: https://nmap.org/data/os-classes.txt
# ---------------------------------------------------------------------------
_NMAP_MAP: dict[str, tuple[str, str] | None] = {
    "general purpose": ("workstation", "unknown"),
    "router": ("network", "router"),
    "switch": ("network", "switch"),
    "firewall": ("network", "firewall"),
    "bridge": ("network", "bridge"),
    "broadband router": ("network", "router"),
    "wap": ("network", "wireless_access_point"),
    "printer": ("printer", "unknown"),
    "print server": ("printer", "print_server"),
    "terminal": ("workstation", "thin_client"),
    "terminal server": ("server", "unknown"),
    "remote management": ("server", "unknown"),
    "telecom-misc": ("network", "unknown"),
    "voip adapter": ("network", "voip"),
    "voip phone": ("mobile", "phone"),
    "pbx": ("network", "pbx"),
    "power-device": ("ot", "unknown"),
    "storage-misc": ("storage", "unknown"),
    "media device": ("iot", "unknown"),
    "webcam": ("iot", "unknown"),
    "load balancer": ("network", "load_balancer"),
    "proxy server": ("network", "unknown"),
    "game console": ("iot", "unknown"),
    "specialized": None,  # too generic
    "hub": ("network", "hub"),
    "phone": ("mobile", "phone"),
}

# ---------------------------------------------------------------------------
# NetBox device-role / device-type context mappings
# Patch panels, cable management, and PDUs without active management are
# infrastructure components that don't fit our asset categories — return None.
# ---------------------------------------------------------------------------
_NETBOX_MAP: dict[str, tuple[str, str] | None] = {
    # Compute
    "server": ("server", "unknown"),
    "virtual machine": ("server", "vm"),
    "vm": ("server", "vm"),
    "hypervisor": ("server", "unknown"),
    # Networking
    "router": ("network", "router"),
    "switch": ("network", "switch"),
    "firewall": ("network", "firewall"),
    "load balancer": ("network", "load_balancer"),
    "wireless access point": ("network", "wireless_access_point"),
    "access point": ("network", "wireless_access_point"),
    "wap": ("network", "wireless_access_point"),
    "bridge": ("network", "bridge"),
    "hub": ("network", "hub"),
    "wan optimizer": ("network", "unknown"),
    "vpn concentrator": ("network", "unknown"),
    "console server": ("server", "unknown"),
    # Storage
    "storage": ("storage", "unknown"),
    "nas": ("storage", "nas"),
    "san": ("storage", "san"),
    "tape library": ("storage", "tape"),
    # Printing
    "printer": ("printer", "unknown"),
    "mfp": ("printer", "mfp"),
    "print server": ("printer", "print_server"),
    # Workstation / endpoints
    "workstation": ("workstation", "unknown"),
    "desktop": ("workstation", "desktop"),
    "laptop": ("workstation", "laptop"),
    "thin client": ("workstation", "thin_client"),
    # Mobile
    "phone": ("mobile", "phone"),
    "tablet": ("mobile", "tablet"),
    "mobile device": ("mobile", "unknown"),
    "smartphone": ("mobile", "phone"),
    # IoT
    "iot": ("iot", "unknown"),
    "media device": ("iot", "unknown"),
    "ip camera": ("iot", "security_camera"),
    "camera": ("iot", "security_camera"),
    # OT
    "plc": ("ot", "plc"),
    "scada": ("ot", "scada"),
    "industrial controller": ("ot", "plc"),
    # Infrastructure — not categorizable as IT assets
    "patch panel": None,
    "cable management": None,
    "pdu": None,
    "kvm switch": None,
    "rack": None,
}

# ---------------------------------------------------------------------------
# Fingerbank device-type name mappings
# Source: https://fingerbank.inverse.ca/
# ---------------------------------------------------------------------------
_FINGERBANK_MAP: dict[str, tuple[str, str] | None] = {
    # Networking
    "switch/router": ("network", "switch"),
    "router": ("network", "router"),
    "switch": ("network", "switch"),
    "firewall": ("network", "firewall"),
    "wireless lan controller": ("network", "unknown"),
    "access point": ("network", "wireless_access_point"),
    "wireless access point": ("network", "wireless_access_point"),
    "load balancer": ("network", "load_balancer"),
    "vpn concentrator": ("network", "unknown"),
    "voip phone": ("mobile", "phone"),
    "voip adapter": ("network", "voip"),
    "ip phone": ("mobile", "phone"),
    # Compute
    "server": ("server", "unknown"),
    "nas": ("storage", "nas"),
    "storage device": ("storage", "unknown"),
    # OT/ICS
    "programmable logic controller (plc)": ("ot", "plc"),
    "plc": ("ot", "plc"),
    "scada": ("ot", "scada"),
    "industrial device": ("ot", "unknown"),
    "industrial controller": ("ot", "plc"),
    "hvac": ("ot", "hvac"),
    "building automation": ("ot", "unknown"),
    # Endpoints
    "workstation": ("workstation", "unknown"),
    "desktop": ("workstation", "desktop"),
    "laptop": ("workstation", "laptop"),
    "thin client": ("workstation", "thin_client"),
    # Mobile
    "phone": ("mobile", "phone"),
    "tablet": ("mobile", "tablet"),
    "mobile device": ("mobile", "unknown"),
    "smartphone": ("mobile", "phone"),
    # Printing
    "printer": ("printer", "unknown"),
    "multifunction printer": ("printer", "mfp"),
    "mfp": ("printer", "mfp"),
    # IoT
    "ip camera": ("iot", "security_camera"),
    "camera": ("iot", "security_camera"),
    "media player": ("iot", "unknown"),
    "game console": ("iot", "unknown"),
    "smart tv": ("iot", "unknown"),
    "iot device": ("iot", "unknown"),
    "embedded device": ("iot", "embedded_pc"),
}

# ---------------------------------------------------------------------------
# Rapid7 Recog device type mappings
# Source: https://github.com/rapid7/recog
# Recog uses explicit hw.device/os.device fields from protocol banner matching.
# ---------------------------------------------------------------------------
_RECOG_MAP: dict[str, tuple[str, str] | None] = {
    "router": ("network", "router"),
    "switch": ("network", "switch"),
    "firewall": ("network", "firewall"),
    "wap": ("network", "wireless_access_point"),
    "printer": ("printer", "unknown"),
    "multifunction device": ("printer", "unknown"),
    "print server": ("printer", "print_server"),
    "voip": ("network", "voip"),
    "voip phone": ("mobile", "phone"),
    "ip camera": ("iot", "unknown"),
    "media gateway": ("network", "unknown"),
    "web proxy": ("network", "unknown"),
    "load balancer": ("network", "load_balancer"),
    "thin client": ("workstation", "thin_client"),
    "kvm": ("server", "unknown"),
    "ups": ("ot", "unknown"),
    "plc": ("ot", "plc"),
    "dslam": ("network", "unknown"),
    "general purpose": ("workstation", "unknown"),
    "server": ("server", "unknown"),
}

_SOURCE_MAPS: dict[str, dict[str, tuple[str, str] | None]] = {
    "nmap": _NMAP_MAP,
    "netbox": _NETBOX_MAP,
    "fingerbank": _FINGERBANK_MAP,
    "recog": _RECOG_MAP,
}


def map_device_type(device_type: str, source: str) -> tuple[str, str] | None:
    """Map an external device type label to a canonical (category, subcategory) tuple.

    Args:
        device_type: The device type string from the external source.
        source: One of "nmap", "netbox", "fingerbank".

    Returns:
        A (category, subcategory) tuple, or None when the type is unmappable
        (too generic, infrastructure-only, or unknown to this source).
    """
    source_map = _SOURCE_MAPS.get(source.lower())
    if source_map is None:
        return None

    key = device_type.strip().lower()
    if key not in source_map:
        return None

    return source_map[key]
