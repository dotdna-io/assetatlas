# SPDX-License-Identifier: AGPL-3.0-or-later
"""
hwdata vendor name scraper.

Clones https://github.com/vcrhonek/hwdata.git and parses ``pci.ids`` and
``usb.ids`` to extract vendor aliases.

File format (both files share the same format):
  Lines with NO leading whitespace define vendors:
    ``<hex_id>  <vendor_name>``   (two spaces between id and name)
  Lines with leading whitespace define devices/subsystems — skipped.
  Lines starting with ``#`` are comments — skipped.

Output record shape:
  vendor_id, manufacturer, id_type, source

  id_type is "pci" or "usb".

Usage:
    from data_scrapers.device_knowledge.scrapers.hwdata_vendors import scrape, parse_ids_file
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_URL = "https://github.com/vcrhonek/hwdata.git"
_REPO_DIR = "hwdata"
_SOURCE = "hwdata"

_VENDOR_SUFFIXES = re.compile(
    r"\b(corp\.?|corporation|inc\.?|incorporated|ltd\.?|limited|llc\.?|"
    r"co\.?|gmbh|s\.?a\.?|ag|bv|nv|plc\.?|technologies|technology|"
    r"systems|solutions|networks|electronics|semiconductor|"
    r"international|group|holdings)\b",
    re.IGNORECASE,
)

# Vendor line: starts with exactly 4 hex digits followed by two or more spaces
_VENDOR_LINE = re.compile(r"^([0-9a-fA-F]{4})\s{2,}(.+)$")


def _normalize_vendor(name: str) -> str:
    """Lowercase, strip legal suffixes, collapse whitespace."""
    name = name.strip().lower()
    name = _VENDOR_SUFFIXES.sub("", name)
    name = re.sub(r"[,.]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def parse_ids_file(lines: list[str], id_type: str = "pci") -> list[dict[str, Any]]:
    """Parse a ``pci.ids`` or ``usb.ids`` file and return vendor-only records.

    Only top-level vendor lines (no leading whitespace, matching
    ``<hex_id>  <vendor_name>``) are returned.  Sub-device and subsystem lines
    are skipped.

    Args:
        lines: Raw text lines from the .ids file.
        id_type: ``"pci"`` or ``"usb"`` — recorded in the ``id_type`` field.

    Returns:
        List of dicts with keys: vendor_id, manufacturer, id_type, source.
    """
    seen: set[str] = set()
    records: list[dict[str, Any]] = []

    for raw in lines:
        # Skip comments and blank lines
        if not raw.strip() or raw.startswith("#"):
            continue
        # Skip device/subsystem lines (leading whitespace)
        if raw[0] in (" ", "\t"):
            continue

        m = _VENDOR_LINE.match(raw)
        if not m:
            continue

        vendor_id = m.group(1).lower()
        vendor_name_raw = m.group(2)

        manufacturer = _normalize_vendor(vendor_name_raw)
        if not manufacturer:
            continue

        if vendor_id in seen:
            continue
        seen.add(vendor_id)

        records.append(
            {
                "vendor_id": vendor_id,
                "manufacturer": manufacturer,
                "id_type": id_type,
                "source": _SOURCE,
            }
        )

    return records


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Clone (or reuse) the hwdata repo and parse pci.ids + usb.ids.

    Args:
        cache_dir: Directory used to cache the git clone.

    Returns:
        List of record dicts with keys: vendor_id, manufacturer, id_type, source.
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

    records: list[dict[str, Any]] = []

    for ids_filename, id_type in [("pci.ids", "pci"), ("usb.ids", "usb")]:
        ids_file = repo_path / ids_filename
        if not ids_file.exists():
            logger.warning("%s not found in %s", ids_filename, repo_path)
            continue
        lines = ids_file.read_text(encoding="utf-8", errors="replace").splitlines()
        file_records = parse_ids_file(lines, id_type=id_type)
        logger.info("hwdata %s: parsed %d vendor entries", ids_filename, len(file_records))
        records.extend(file_records)

    logger.info("hwdata total: %d vendor entries", len(records))
    return records
