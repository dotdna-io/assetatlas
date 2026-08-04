# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Manufacturer name normalization.

Maps known manufacturer name variants to canonical forms.
"""

from typing import Optional, List

# Canonical name -> list of known aliases (all lowercase for matching)
_CANONICAL_MANUFACTURERS = {
    "Dell": ["dell", "dell inc.", "dell inc", "dell technologies"],
    "HP": ["hp", "hewlett packard", "hp inc.", "hp inc", "hewlett-packard", "hewlett packard company"],
    "HPE": ["hpe", "hewlett packard enterprise"],
    "Lenovo": ["lenovo"],
    "Microsoft": ["microsoft", "microsoft corp.", "microsoft corp", "microsoft corporation"],
    "Apple": ["apple", "apple inc.", "apple inc", "apple computer"],
    "Samsung": ["samsung", "samsung electronics", "samsung electronics co."],
    "Cisco": ["cisco", "cisco systems", "cisco systems, inc.", "cisco systems inc", "cisco systems, inc"],
    "VMware": ["vmware", "vmware, inc.", "vmware inc"],
    "Wistron": ["wistron", "wistron corporation"],
    "Intel": ["intel", "intel corporation", "intel corp"],
    "Juniper Networks": ["juniper networks", "juniper", "juniper networks, inc.", "juniper networks, inc"],
    "Palo Alto Networks": ["palo alto networks", "palo alto"],
    "Rockwell Automation": ["rockwell automation", "rockwell", "allen-bradley"],
    "Schneider Electric": ["schneider electric", "schneider"],
    "Siemens": ["siemens", "siemens ag"],
    "Honeywell": ["honeywell", "honeywell international"],
    "Zebra Technologies": ["zebra technologies", "zebra"],
    "Realtek": ["realtek", "realtek semiconductor"],
    "ASUSTeK": ["asustek", "asustek computer inc.", "asus"],
    "Inventec": ["inventec"],
    "Micro-Star": ["micro-star intl", "micro-star", "msi"],
    "APC": ["apc", "american power conversion", "american power conversion corp", "apc by schneider electric"],
    "Bcom Electronics": ["bcom electronics"],
    "Mist Systems": ["mist systems"],
    "Axis Communications": ["axis communications", "axis communications ab"],
    "Yealink": ["yealink", "yealink network technology"],
}

# Build reverse lookup: lowercase alias -> canonical name
_ALIAS_MAP = {}
for canonical, aliases in _CANONICAL_MANUFACTURERS.items():
    for alias in aliases:
        _ALIAS_MAP[alias] = canonical


def normalize_manufacturer(name: Optional[str]) -> Optional[str]:
    """Normalize a manufacturer name to its canonical form.

    Returns the canonical name if a known alias is found,
    otherwise returns the input title-cased.
    Returns None for empty/None input.
    """
    if not name or not name.strip():
        return None

    lookup = name.strip().lower()
    if lookup in _ALIAS_MAP:
        return _ALIAS_MAP[lookup]

    # Unknown manufacturer -- return title-cased
    return name.strip().title()


def is_known_manufacturer(name: str) -> bool:
    """Return True if name is a known manufacturer alias."""
    return bool(name) and name.strip().lower() in _ALIAS_MAP


def normalize_manufacturer_list(manufacturers: Optional[List[str]]) -> List[str]:
    """Normalize a list of manufacturer names, deduplicating after normalization.

    Preserves first-seen order.
    """
    if not manufacturers:
        return []

    seen = set()
    result = []
    for name in manufacturers:
        normalized = normalize_manufacturer(name)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
