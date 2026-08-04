# SPDX-License-Identifier: AGPL-3.0-or-later
"""
CPE (Common Platform Enumeration) dictionary scraper.

Clones https://github.com/tiiuae/cpedict.git into the cache directory and
parses the vendor/product data for hardware entries.

The cpedict repo provides two CSV-like text files:
  - ``cpe23_vendor_product.csv``  (preferred)
  - Falls back to walking ``*.json`` / any matching flat file if the CSV is absent.

CPE naming scheme:  cpe:2.3:h:<vendor>:<product>:<version>:...
  Part ``h`` means hardware.

Output record shape:
  manufacturer, model, source

Usage:
    from data_scrapers.device_knowledge.scrapers.cpe_dictionary import scrape, parse_cpe_lines
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_URL = "https://github.com/tiiuae/cpedict.git"
_REPO_DIR = "cpedict"
_SOURCE = "cpe"

_VENDOR_SUFFIXES = re.compile(
    r"\b(corp\.?|corporation|inc\.?|incorporated|ltd\.?|limited|llc\.?|"
    r"co\.?|gmbh|s\.?a\.?|ag|bv|nv|plc\.?|technologies|technology|"
    r"systems|solutions|networks|electronics|semiconductor|"
    r"international|group|holdings)\b",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    """Lowercase, strip underscores/hyphens to spaces, collapse whitespace."""
    name = name.replace("_", " ").replace("-", " ").strip().lower()
    name = _VENDOR_SUFFIXES.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def parse_cpe_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Parse CPE 2.3 formatted lines and return hardware records.

    Accepts lines in the CPE 2.3 URI format:
      ``cpe:2.3:h:<vendor>:<product>:<version>:...``

    Lines where the third component is ``h`` (hardware) are returned.
    Lines that are comments (``#``) or empty are skipped.

    Args:
        lines: Raw text lines from a CPE dictionary file.

    Returns:
        List of dicts with keys: manufacturer, model, source.
    """
    seen: set[tuple[str, str]] = set()
    records: list[dict[str, Any]] = []

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Support CPE URIs, tab-separated, and quoted CSV formats
        if line.startswith("cpe:2.3:"):
            parts = line.split(":")
            # cpe:2.3:part:vendor:product:version:...
            if len(parts) < 5:
                continue
            part = parts[2]  # 'h' for hardware
            if part != "h":
                continue
            vendor_raw = parts[3]
            product_raw = parts[4]
        elif "\t" in line:
            # Flat vendor<TAB>product format (used in cpedict repo exports)
            tab_parts = line.split("\t", 1)
            vendor_raw = tab_parts[0]
            product_raw = tab_parts[1] if len(tab_parts) > 1 else ""
        elif "," in line:
            # Quoted CSV format: "vendor","product"
            csv_parts = line.split(",", 1)
            vendor_raw = csv_parts[0].strip().strip('"')
            product_raw = csv_parts[1].strip().strip('"') if len(csv_parts) > 1 else ""
        else:
            continue

        vendor = _normalize(vendor_raw)
        product = _normalize(product_raw)
        if not vendor or vendor in ("*", "-", "na"):
            continue

        key = (vendor, product)
        if key in seen:
            continue
        seen.add(key)

        records.append(
            {
                "manufacturer": vendor,
                "model": product,
                "source": _SOURCE,
            }
        )

    return records


def _iter_cpe_lines(repo_path: Path) -> list[str]:
    """Return raw lines from the best available file in the cpedict repo."""
    # Preferred: pre-built CSV with cpe23 entries
    candidates = [
        repo_path / "cpe23_vendor_product.csv",
        repo_path / "cpe_dict.xml",
        repo_path / "data" / "cpes.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            logger.info("Reading CPE data from %s", candidate)
            return candidate.read_text(encoding="utf-8", errors="replace").splitlines()

    # Fall back: find any .txt or .csv file (root then data/)
    search_dirs = [repo_path, repo_path / "data"]
    for search_dir in search_dirs:
        for txt_file in sorted(search_dir.glob("*.txt")) + sorted(search_dir.glob("*.csv")):
            logger.info("Reading CPE data from %s", txt_file)
            return txt_file.read_text(encoding="utf-8", errors="replace").splitlines()

    # Last resort: scan all .xml files for cpe:2.3:h: entries
    all_lines: list[str] = []
    for xml_file in sorted(repo_path.rglob("*.xml")):
        lines = xml_file.read_text(encoding="utf-8", errors="replace").splitlines()
        all_lines.extend(l for l in lines if "cpe:2.3:h:" in l)
        if len(all_lines) > 200_000:
            break
    return all_lines


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Clone (or reuse) the CPE dictionary repo and return hardware records.

    Args:
        cache_dir: Directory used to cache the git clone.

    Returns:
        List of record dicts with keys: manufacturer, model, source.
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

    lines = _iter_cpe_lines(repo_path)
    records = parse_cpe_lines(lines)
    logger.info("CPE: parsed %d hardware records", len(records))
    return records
