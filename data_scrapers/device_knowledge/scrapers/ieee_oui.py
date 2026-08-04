# SPDX-License-Identifier: AGPL-3.0-or-later
"""
IEEE OUI (Organizationally Unique Identifier) scraper.

Downloads the MAC address vendor CSV database from
https://maclookup.app/downloads/csv-database/get-db and parses
(mac_prefix, vendor_name) pairs.

Output record shape:
  mac_prefix, manufacturer, source

Usage:
    from data_scrapers.device_knowledge.scrapers.ieee_oui import scrape, parse_oui_csv
"""

from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SOURCE_URL = "https://maclookup.app/downloads/csv-database/get-db"
_CACHE_FILENAME = "ieee_oui.csv"
_SOURCE = "ieee_oui"

_VENDOR_SUFFIXES = re.compile(
    r"\b(corp\.?|corporation|inc\.?|incorporated|ltd\.?|limited|llc\.?|"
    r"co\.?|gmbh|s\.?a\.?|ag|bv|nv|plc\.?|technologies|technology|"
    r"systems|solutions|networks|electronics|semiconductor|"
    r"international|group|holdings)\b",
    re.IGNORECASE,
)


def _normalize_vendor(name: str) -> str:
    """Lowercase, strip legal suffixes, collapse whitespace."""
    name = name.strip().lower()
    name = _VENDOR_SUFFIXES.sub("", name)
    name = re.sub(r"[,.]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _normalize_mac_prefix(prefix: str) -> str:
    """Normalise MAC prefix to uppercase colon-separated format (AA:BB:CC)."""
    # Strip any separators and reformat
    hex_only = re.sub(r"[:\-.]", "", prefix).upper()
    # Pad to at least 6 hex digits (3-byte OUI)
    hex_only = hex_only[:6].ljust(6, "0")
    return f"{hex_only[0:2]}:{hex_only[2:4]}:{hex_only[4:6]}"


def parse_oui_csv(lines: list[str]) -> list[dict[str, Any]]:
    """Parse IEEE OUI CSV lines into (mac_prefix, manufacturer) records.

    The CSV format from maclookup.app is:
      macPrefix,vendorName,private,blockType,lastUpdate

    Lines starting with ``#`` or ``macPrefix`` (header) are skipped.

    Args:
        lines: Raw text lines from the CSV download.

    Returns:
        List of dicts with keys: mac_prefix, manufacturer, source.
    """
    seen: set[str] = set()
    records: list[dict[str, Any]] = []

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.lower().replace(" ", "").startswith("macprefix"):
            continue

        # Split on comma — vendor name may contain commas, so limit splits
        parts = line.split(",", 4)
        if len(parts) < 2:
            continue

        mac_raw = parts[0].strip()
        vendor_raw = parts[1].strip().strip('"')

        if not mac_raw or not vendor_raw:
            continue

        # Skip private registrations
        if len(parts) >= 3 and parts[2].strip().lower() in ("true", "1", "yes"):
            continue

        try:
            mac_prefix = _normalize_mac_prefix(mac_raw)
        except Exception:  # noqa: BLE001
            continue

        manufacturer = _normalize_vendor(vendor_raw)
        if not manufacturer:
            continue

        if mac_prefix in seen:
            continue
        seen.add(mac_prefix)

        records.append(
            {
                "mac_prefix": mac_prefix,
                "manufacturer": manufacturer,
                "source": _SOURCE,
            }
        )

    return records


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Download (with local caching) and parse the IEEE OUI CSV database.

    Args:
        cache_dir: Directory used to cache the downloaded CSV file.

    Returns:
        List of record dicts with keys: mac_prefix, manufacturer, source.
    """
    cache_path = Path(cache_dir) / _CACHE_FILENAME
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if not cache_path.exists():
        logger.info("Downloading OUI database %s -> %s", _SOURCE_URL, cache_path)
        req = urllib.request.Request(_SOURCE_URL, headers={"User-Agent": "AssetAtlas/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            cache_path.write_bytes(resp.read())
    else:
        logger.info("Using cached OUI database: %s", cache_path)

    lines = cache_path.read_text(encoding="utf-8", errors="replace").splitlines()
    records = parse_oui_csv(lines)
    logger.info("IEEE OUI: parsed %d vendor entries", len(records))
    return records
