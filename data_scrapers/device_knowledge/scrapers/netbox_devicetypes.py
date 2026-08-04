# SPDX-License-Identifier: AGPL-3.0-or-later
"""
NetBox device-type library scraper.

Clones https://github.com/netbox-community/devicetype-library.git into the
cache directory (or reuses an existing clone) and walks the ``device-types/``
directory.  Each subdirectory is a manufacturer; each ``.yaml`` file describes
a device model.

Parsed fields per record:
  manufacturer, model, slug, device_type, category, subcategory, source

Usage:
    from data_scrapers.device_knowledge.scrapers.netbox_devicetypes import scrape, parse_device_yaml

    records = scrape(cache_dir="cache/")
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from data_scrapers.device_knowledge.category_mapping import map_device_type

logger = logging.getLogger(__name__)

_REPO_URL = "https://github.com/netbox-community/devicetype-library.git"
_REPO_DIR = "netbox-devicetype-library"
_SOURCE = "netbox"

# Heuristic keywords used to infer a NetBox device type from slug/comments
_KEYWORD_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brouter\b", re.IGNORECASE), "router"),
    (re.compile(r"\bswitch\b", re.IGNORECASE), "switch"),
    (re.compile(r"\bfirewall\b", re.IGNORECASE), "firewall"),
    (re.compile(r"\baccess[\s_-]*point\b|\\bwap\b", re.IGNORECASE), "access point"),
    (re.compile(r"\bload[\s_-]*balancer\b", re.IGNORECASE), "load balancer"),
    (re.compile(r"\bnas\b", re.IGNORECASE), "nas"),
    (re.compile(r"\bsan\b", re.IGNORECASE), "san"),
    (re.compile(r"\btape[\s_-]*library\b", re.IGNORECASE), "tape library"),
    (re.compile(r"\bstorage\b", re.IGNORECASE), "storage"),
    (re.compile(r"\bprinter\b", re.IGNORECASE), "printer"),
    (re.compile(r"\bmfp\b|multifunction", re.IGNORECASE), "mfp"),
    (re.compile(r"\bprint[\s_-]*server\b", re.IGNORECASE), "print server"),
    (re.compile(r"\bserver\b", re.IGNORECASE), "server"),
    (re.compile(r"\blaptop\b", re.IGNORECASE), "laptop"),
    (re.compile(r"\bdesktop\b", re.IGNORECASE), "desktop"),
    (re.compile(r"\bthin[\s_-]*client\b", re.IGNORECASE), "thin client"),
    (re.compile(r"\bworkstation\b", re.IGNORECASE), "workstation"),
    (re.compile(r"\bphone\b|\bip[\s_-]*phone\b", re.IGNORECASE), "phone"),
    (re.compile(r"\btablet\b", re.IGNORECASE), "tablet"),
    (re.compile(r"\bcamera\b", re.IGNORECASE), "camera"),
    (re.compile(r"\bplc\b", re.IGNORECASE), "plc"),
    (re.compile(r"\bscada\b", re.IGNORECASE), "scada"),
    (re.compile(r"\bpdu\b", re.IGNORECASE), "pdu"),
    (re.compile(r"\bpatch[\s_-]*panel\b", re.IGNORECASE), "patch panel"),
    (re.compile(r"\brack\b", re.IGNORECASE), "rack"),
    (re.compile(r"\bkvm\b", re.IGNORECASE), "kvm switch"),
]


def _infer_device_type(slug: str, comments: str) -> str | None:
    """Return the best-guess NetBox device type key from slug and comments text."""
    text = f"{slug} {comments}"
    for pattern, device_type in _KEYWORD_MAP:
        if pattern.search(text):
            return device_type
    return None


def parse_device_yaml(
    manufacturer: str,
    yaml_text: str,
    filename: str = "",
) -> dict[str, Any] | None:
    """Parse a single NetBox device-type YAML file (as raw text).

    Uses a minimal hand-rolled parser to avoid a hard ``pyyaml`` dependency at
    import time.  Only ``model``, ``slug``, and ``comments`` top-level fields
    are extracted.

    Returns a record dict or None if the entry cannot be categorised.
    """
    model = ""
    slug = filename.replace(".yaml", "").replace(".yml", "")
    comments = ""

    for line in yaml_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("model:"):
            model = stripped[len("model:"):].strip().strip("\"'")
        elif stripped.startswith("slug:"):
            slug = stripped[len("slug:"):].strip().strip("\"'")
        elif stripped.startswith("comments:"):
            comments = stripped[len("comments:"):].strip().strip("\"'")

    device_type_key = _infer_device_type(slug, comments)
    if device_type_key is None:
        return None

    mapping = map_device_type(device_type_key, source=_SOURCE)
    if mapping is None:
        return None

    category, subcategory = mapping
    return {
        "manufacturer": manufacturer.strip(),
        "model": model,
        "slug": slug,
        "device_type": device_type_key,
        "category": category,
        "subcategory": subcategory,
        "source": _SOURCE,
    }


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Clone (or reuse) the NetBox device-type library and parse all YAML files.

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

    device_types_root = repo_path / "device-types"
    if not device_types_root.is_dir():
        logger.warning("device-types/ directory not found in %s", repo_path)
        return []

    records: list[dict[str, Any]] = []
    for mfr_dir in sorted(device_types_root.iterdir()):
        if not mfr_dir.is_dir():
            continue
        manufacturer = mfr_dir.name
        for yaml_file in sorted(mfr_dir.glob("*.yaml")):
            try:
                text = yaml_file.read_text(encoding="utf-8", errors="replace")
                record = parse_device_yaml(manufacturer, text, filename=yaml_file.stem)
                if record is not None:
                    records.append(record)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping %s: %s", yaml_file, exc)

    logger.info("NetBox: parsed %d categorisable records", len(records))
    return records
