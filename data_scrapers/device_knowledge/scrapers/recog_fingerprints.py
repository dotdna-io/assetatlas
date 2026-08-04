# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Rapid7 Recog fingerprint scraper.

Clones https://github.com/rapid7/recog.git and parses all XML files in the
``xml/`` directory.  Each fingerprint may carry static ``param`` elements that
classify the device hardware: ``hw.device`` / ``os.device`` (type),
``hw.vendor`` / ``os.vendor`` (manufacturer), ``hw.product`` / ``os.product``
(product name).

Only params with ``pos="0"`` **and** a ``value`` attribute are used — those are
static classifications baked into the fingerprint, not regex-captured fields.

Output record shape:
  manufacturer, model, device_type, category, subcategory, source

Usage:
    from data_scrapers.device_knowledge.scrapers.recog_fingerprints import scrape, parse_recog_xml
"""

from __future__ import annotations

import logging
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from data_scrapers.device_knowledge.category_mapping import map_device_type

logger = logging.getLogger(__name__)

_REPO_URL = "https://github.com/rapid7/recog.git"
_REPO_DIR = "recog"
_SOURCE = "recog"

# Param name prefixes we care about
_DEVICE_PARAMS = {"hw.device", "os.device"}
_VENDOR_PARAMS = {"hw.vendor", "os.vendor"}
_PRODUCT_PARAMS = {"hw.product", "os.product", "hw.model"}


def parse_recog_xml(xml_content: str) -> list[dict[str, Any]]:
    """Parse a single Recog XML file and return classifiable fingerprint records.

    Only fingerprints that have at least one static device-type param
    (``hw.device`` or ``os.device`` with a ``value`` attribute and ``pos="0"``)
    are returned.  Results are deduplicated by (vendor, product, device_type).

    Args:
        xml_content: Raw XML text of one Recog fingerprint file.

    Returns:
        List of dicts with keys: manufacturer, model, device_type,
        category, subcategory, source.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        logger.warning("Failed to parse Recog XML: %s", exc)
        return []

    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for fingerprint in root.iter("fingerprint"):
        vendor = ""
        product = ""
        device_type = ""

        for param in fingerprint.iter("param"):
            pos = param.get("pos", "")
            name = param.get("name", "")
            value = param.get("value", "")

            # Only static (pos="0") params with an explicit value
            if pos != "0" or not value:
                continue

            if name in _DEVICE_PARAMS and not device_type:
                device_type = value
            elif name in _VENDOR_PARAMS and not vendor:
                vendor = value
            elif name in _PRODUCT_PARAMS and not product:
                product = value

        if not device_type:
            continue

        mapping = map_device_type(device_type, source=_SOURCE)
        if mapping is None:
            continue

        category, subcategory = mapping

        dedup_key = (vendor.lower(), product.lower(), device_type.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        records.append(
            {
                "manufacturer": vendor,
                "model": product,
                "device_type": device_type,
                "category": category,
                "subcategory": subcategory,
                "source": _SOURCE,
            }
        )

    return records


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Clone (or reuse) the Recog repo and parse all XML fingerprint files.

    Args:
        cache_dir: Directory used to cache the git clone.

    Returns:
        List of record dicts deduplicated across all XML files.
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

    xml_dir = repo_path / "xml"
    if not xml_dir.is_dir():
        logger.warning("xml/ directory not found in %s", repo_path)
        return []

    all_records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for xml_file in sorted(xml_dir.glob("*.xml")):
        try:
            content = xml_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        for record in parse_recog_xml(content):
            dedup_key = (
                record["manufacturer"].lower(),
                record["model"].lower(),
                record["device_type"].lower(),
            )
            if dedup_key not in seen:
                seen.add(dedup_key)
                all_records.append(record)

    logger.info("Recog: parsed %d categorisable records from %s", len(all_records), xml_dir)
    return all_records
