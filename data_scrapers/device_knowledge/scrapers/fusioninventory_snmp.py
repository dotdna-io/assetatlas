# SPDX-License-Identifier: AGPL-3.0-or-later
"""
FusionInventory SNMP sysObjectID scraper.

Clones https://github.com/fusioninventory/sysobject.ids.git and parses the
``sysobject.ids`` text file which maps SNMP sysObjectID OIDs to manufacturer
and device type strings.

File format (pipe-separated):
  OID|manufacturer|device_type|model

Output record shape:
  oid, manufacturer, device_type, category, subcategory, source

Usage:
    from data_scrapers.device_knowledge.scrapers.fusioninventory_snmp import scrape, parse_sysobject_ids
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from data_scrapers.device_knowledge.category_mapping import map_device_type

logger = logging.getLogger(__name__)

_REPO_URL = "https://github.com/fusioninventory/sysobject.ids.git"
_REPO_DIR = "fusioninventory-sysobject-ids"
_IDS_FILENAME = "sysobject.ids"
_SOURCE = "fusioninventory"

# Map FusionInventory device type strings to our canonical keys
# FusionInventory uses a limited vocabulary; we map to NetBox-compatible keys
# so we can reuse the _NETBOX_MAP in category_mapping.
_FI_TO_NETBOX: dict[str, str] = {
    "networking": "switch",
    "network": "switch",
    "switch": "switch",
    "router": "router",
    "firewall": "firewall",
    "printer": "printer",
    "workstation": "workstation",
    "server": "server",
    "storage": "storage",
    "ups": "pdu",
    "power": "pdu",
    "phone": "phone",
    "voip": "phone",
    "camera": "camera",
    "ap": "access point",
    "wireless": "access point",
    "load balancer": "load balancer",
    "wan optimizer": "wan optimizer",
    "hub": "hub",
    "bridge": "bridge",
    "tape": "tape library",
    "nas": "nas",
    "san": "san",
    "plc": "plc",
    "scada": "scada",
    "copier": "mfp",
    "scanner": "printer",
}

_VENDOR_SUFFIXES = re.compile(
    r"\b(corp\.?|corporation|inc\.?|incorporated|ltd\.?|limited|llc\.?|"
    r"co\.?|gmbh|s\.?a\.?|ag|bv|nv|plc\.?|technologies|technology|"
    r"systems|solutions|networks|electronics|semiconductor|"
    r"international|group|holdings)\b",
    re.IGNORECASE,
)


def _normalize_vendor(name: str) -> str:
    name = name.strip().lower()
    name = _VENDOR_SUFFIXES.sub("", name)
    name = re.sub(r"[,.]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _map_fi_type(fi_type: str) -> tuple[str, str] | None:
    """Translate a FusionInventory device type to a canonical (category, subcategory)."""
    key = fi_type.strip().lower()
    netbox_key = _FI_TO_NETBOX.get(key)
    if netbox_key is None:
        # Try partial match
        for fi_key, nb_key in _FI_TO_NETBOX.items():
            if fi_key in key or key in fi_key:
                netbox_key = nb_key
                break
    if netbox_key is None:
        return None
    return map_device_type(netbox_key, source="netbox")


def parse_sysobject_ids(lines: list[str]) -> list[dict[str, Any]]:
    """Parse ``sysobject.ids`` lines into structured records.

    The file uses pipe-separated fields:
      OID|Manufacturer|DeviceType[|Model]

    Lines starting with ``#`` or blank lines are skipped.

    Args:
        lines: Raw text lines from the sysobject.ids file.

    Returns:
        List of dicts with keys: oid, manufacturer, device_type, category,
        subcategory, source.
    """
    records: list[dict[str, Any]] = []

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 3:
            continue

        oid = parts[0]
        manufacturer_raw = parts[1]
        fi_type = parts[2]

        manufacturer = _normalize_vendor(manufacturer_raw)
        if not manufacturer:
            continue

        mapping = _map_fi_type(fi_type)
        if mapping is None:
            continue

        category, subcategory = mapping
        records.append(
            {
                "oid": oid,
                "manufacturer": manufacturer,
                "device_type": fi_type.lower(),
                "category": category,
                "subcategory": subcategory,
                "source": _SOURCE,
            }
        )

    return records


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Clone (or reuse) the FusionInventory sysobject.ids repo and parse it.

    Args:
        cache_dir: Directory used to cache the git clone.

    Returns:
        List of record dicts.
    """
    repo_path = Path(cache_dir) / _REPO_DIR
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    if not repo_path.exists():
        logger.info("Cloning %s -> %s", _REPO_URL, repo_path)
        subprocess.run(
            ["git", "clone", "--depth=1", _REPO_URL, str(repo_path)],
            check=True,
            capture_output=True,
        )
    else:
        logger.info("Using cached clone: %s", repo_path)

    ids_file = repo_path / _IDS_FILENAME
    if not ids_file.exists():
        # Search recursively in case directory layout differs
        candidates = list(repo_path.rglob(_IDS_FILENAME))
        if candidates:
            ids_file = candidates[0]
        else:
            logger.warning("sysobject.ids not found in %s", repo_path)
            return []

    lines = ids_file.read_text(encoding="utf-8", errors="replace").splitlines()
    records = parse_sysobject_ids(lines)
    logger.info("FusionInventory: parsed %d categorisable records", len(records))
    return records
