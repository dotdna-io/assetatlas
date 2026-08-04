# SPDX-License-Identifier: AGPL-3.0-or-later
"""MAC address utilities for filtering virtual/invalid addresses."""

from typing import Optional, List

from src.modules.asset_generation.domain.asset_validator import AssetValidator


def is_virtual_mac(mac: Optional[str]) -> bool:
    """Check if a MAC address is virtual/locally-administered/invalid."""
    if not mac or not mac.strip():
        return True

    mac_clean = mac.strip().lower()
    if mac_clean in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
        return True

    # Reject strings that aren't valid MAC format
    parts = mac_clean.split(':')
    if len(parts) != 6:
        return True
    try:
        for p in parts:
            if len(p) != 2:
                return True
            int(p, 16)
    except ValueError:
        return True

    is_virt, _ = AssetValidator.is_virtual_mac(mac_clean)
    return is_virt


def filter_virtual_macs(macs: Optional[List[str]]) -> List[str]:
    """Filter out virtual/locally-administered MACs from a list."""
    if not macs:
        return []
    return [m for m in macs if not is_virtual_mac(m)]


def canonical_mac_component(value) -> Optional[str]:
    """Canonical MAC form used as an asset-identity component.

    Single source of truth for how a MAC is stored in the `asset` field and in
    asset_id_mappings: strip all non-alphanumerics, lowercase, colon-group the
    first 12 hex chars -> 'xx:xx:xx:xx:xx:xx'. Returns None for blank/unknown/
    too-short input. Both AssetEnricher.preprocess and the merge-denylist encoder
    depend on this being identical.
    """
    if value is None:
        return None
    mac_str = str(value).strip()
    if not mac_str or mac_str.lower() == "unknown":
        return None
    cleaned = "".join(ch for ch in mac_str if ch.isalnum()).lower()
    if len(cleaned) < 12:
        return None
    return ":".join(cleaned[i:i + 2] for i in range(0, 12, 2))
