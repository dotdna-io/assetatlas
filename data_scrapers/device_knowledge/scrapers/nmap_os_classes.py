# SPDX-License-Identifier: AGPL-3.0-or-later
"""
NMAP os-classes.txt scraper.

Parses https://nmap.org/data/os-classes.txt, normalizes vendors, maps device
types to canonical categories, and returns structured records.

Line format (space-separated tokens, no delimiters):
  Vendor [OS [Version]] DeviceType

The device type is identified by matching the longest suffix of each line
against the set of known NMAP device types.

Usage:
    from data_scrapers.device_knowledge.scrapers.nmap_os_classes import scrape, parse_os_classes

    records = scrape(cache_dir="cache/")
    # -> [{"manufacturer": ..., "os": ..., "device_type": ...,
    #       "category": ..., "subcategory": ..., "source": "nmap"}, ...]
"""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from data_scrapers.device_knowledge.category_mapping import map_device_type

logger = logging.getLogger(__name__)

_SOURCE_URL = "https://nmap.org/data/os-classes.txt"
_CACHE_FILENAME = "nmap_os_classes.txt"

# All known NMAP device types (longest first so greedy matching works)
_NMAP_DEVICE_TYPES: list[str] = [
    "broadband router",
    "print server",
    "terminal server",
    "remote management",
    "voip adapter",
    "voip phone",
    "power-device",
    "storage-misc",
    "media device",
    "load balancer",
    "proxy server",
    "game console",
    "general purpose",
    "telecom-misc",
    "specialized",
    "terminal",
    "firewall",
    "printer",
    "storage",
    "bridge",
    "router",
    "switch",
    "webcam",
    "phone",
    "hub",
    "wap",
    "pbx",
]

# Vendor name suffixes to strip during normalization
_VENDOR_SUFFIXES = re.compile(
    r"\b(corp\.?|corporation|inc\.?|incorporated|ltd\.?|limited|llc\.?|"
    r"co\.?|gmbh|s\.?a\.?|ag|bv|nv|plc\.?|technologies|technology|"
    r"systems|solutions|networks|electronics|semiconductor|"
    r"international|group|holdings)\b",
    re.IGNORECASE,
)


def _normalize_vendor(vendor: str) -> str:
    """Lowercase, strip legal suffixes, collapse whitespace."""
    vendor = vendor.strip().lower()
    vendor = _VENDOR_SUFFIXES.sub("", vendor)
    vendor = re.sub(r"[,.]", " ", vendor)
    vendor = re.sub(r"\s+", " ", vendor).strip()
    return vendor


def _match_device_type(line_lower: str) -> tuple[str, str] | None:
    """Return (device_type_key, remainder_before_it) for the first matching suffix."""
    for dt in _NMAP_DEVICE_TYPES:
        if line_lower.endswith(dt):
            remainder = line_lower[: -len(dt)].rstrip()
            return dt, remainder
        # Also allow trailing whitespace variants
        suffix_with_space = " " + dt
        idx = line_lower.rfind(suffix_with_space)
        if idx != -1 and idx + len(suffix_with_space) == len(line_lower):
            remainder = line_lower[:idx].rstrip()
            return dt, remainder
    return None


def parse_os_classes(lines: list[str]) -> list[dict[str, Any]]:
    """Parse raw os-classes.txt lines into structured, normalized records.

    Lines starting with '#' or that are blank are skipped.
    Lines whose device_type maps to None (too generic / unmappable) are skipped.

    Args:
        lines: Raw text lines from os-classes.txt (with or without newlines).

    Returns:
        List of dicts with keys: manufacturer, os, device_type, category,
        subcategory, source.
    """
    records: list[dict[str, Any]] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        line_lower = line.lower()
        match = _match_device_type(line_lower)
        if match is None:
            logger.debug("No device type match for line: %r", line)
            continue

        device_type_key, prefix_lower = match
        mapping = map_device_type(device_type_key, source="nmap")
        if mapping is None:
            # Explicitly unmappable (e.g. "specialized")
            continue

        category, subcategory = mapping

        # Reconstruct prefix from original line (preserve original casing for
        # vendor/os extraction, then normalize vendor separately)
        prefix_original = line[: len(prefix_lower)].strip()

        # The prefix is: Vendor [OS tokens...]
        # We split on whitespace and treat the first token as vendor.
        tokens = prefix_original.split() if prefix_original else []
        if not tokens:
            logger.debug("No vendor/os tokens for line: %r", line)
            continue

        vendor_raw = tokens[0]
        os_tokens = tokens[1:]
        os_str = " ".join(os_tokens) if os_tokens else ""

        manufacturer = _normalize_vendor(vendor_raw)

        records.append(
            {
                "manufacturer": manufacturer,
                "os": os_str,
                "device_type": device_type_key,
                "category": category,
                "subcategory": subcategory,
                "source": "nmap",
            }
        )

    return records


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Download os-classes.txt (with local caching) and return parsed records.

    Args:
        cache_dir: Directory used to cache the downloaded file.  The file is
            re-downloaded only if it is absent from the cache.

    Returns:
        List of dicts (same shape as parse_os_classes output).
    """
    cache_path = Path(cache_dir) / _CACHE_FILENAME
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if not cache_path.exists():
        logger.info("Downloading %s -> %s", _SOURCE_URL, cache_path)
        urllib.request.urlretrieve(_SOURCE_URL, cache_path)  # noqa: S310
    else:
        logger.info("Using cached file: %s", cache_path)

    lines = cache_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return parse_os_classes(lines)
