# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Fingerbank device hierarchy scraper.

Uses the Fingerbank API v2 (https://api.fingerbank.org/api/v2/devices) to
enumerate the full device hierarchy.  Requires an API key from the environment
variable ``FINGERBANK_API_KEY``.

Rate limit: 600 requests/hour.  Results are cached to
``<cache_dir>/fingerbank_devices.json`` so subsequent runs skip the API.

Output record shape:
  manufacturer, device_type, category, subcategory, fingerbank_id, source

Usage:
    from data_scrapers.device_knowledge.scrapers.fingerbank_devices import scrape, parse_devices
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from data_scrapers.device_knowledge.category_mapping import map_device_type

logger = logging.getLogger(__name__)

_API_BASE = "https://api.fingerbank.org/api/v2"
_CACHE_FILENAME = "fingerbank_devices.json"
_SOURCE = "fingerbank"
_RATE_LIMIT_DELAY = 6.1  # seconds between requests (<=600/hour)


def _fetch_page(url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch a single API page and return the JSON body as a list."""
    full_url = f"{url}?key={api_key}"
    req = urllib.request.Request(full_url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _fetch_all_devices(api_key: str) -> list[dict[str, Any]]:
    """Paginate through the Fingerbank /devices endpoint."""
    url = f"{_API_BASE}/devices"
    all_devices: list[dict[str, Any]] = []
    page = 1

    while True:
        full_url = f"{url}?key={api_key}&page={page}&per_page=100"
        req = urllib.request.Request(full_url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fingerbank API request failed (page %d): %s", page, exc)
            break

        if not body:
            break

        if isinstance(body, list):
            if not body:
                break
            all_devices.extend(body)
            if len(body) < 100:
                break
            page += 1
        elif isinstance(body, dict):
            items = body.get("devices", body.get("results", []))
            all_devices.extend(items)
            if not body.get("next"):
                break
            page += 1
        else:
            break

        time.sleep(_RATE_LIMIT_DELAY)

    return all_devices


def parse_devices(raw_devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw Fingerbank device dicts to normalised records.

    Each raw device has at minimum a ``name`` field.  The name is used as the
    device type key for category mapping.

    Args:
        raw_devices: List of raw dicts from the Fingerbank API or cached JSON.

    Returns:
        List of record dicts with keys:
        manufacturer, device_type, category, subcategory, fingerbank_id, source.
    """
    records: list[dict[str, Any]] = []

    for device in raw_devices:
        name: str = device.get("name", "").strip()
        if not name:
            continue

        # Extract optional parent name to aid mapping (e.g. parent = "Cisco")
        parent_name: str = ""
        if "parent" in device and isinstance(device["parent"], dict):
            parent_name = device["parent"].get("name", "")

        device_type_key = name.lower()
        mapping = map_device_type(device_type_key, source=_SOURCE)

        # Try parent name as fallback device type
        if mapping is None and parent_name:
            mapping = map_device_type(parent_name.lower(), source=_SOURCE)
            if mapping is not None:
                device_type_key = parent_name.lower()

        if mapping is None:
            continue

        category, subcategory = mapping
        records.append(
            {
                "manufacturer": parent_name.strip() if parent_name else "",
                "device_type": device_type_key,
                "category": category,
                "subcategory": subcategory,
                "fingerbank_id": device.get("id", ""),
                "source": _SOURCE,
            }
        )

    return records


def scrape(cache_dir: str = "cache/") -> list[dict[str, Any]]:
    """Fetch Fingerbank device hierarchy (with caching) and return parsed records.

    Reads ``FINGERBANK_API_KEY`` from the environment.  If the key is absent
    the function logs a warning and returns an empty list.

    Args:
        cache_dir: Directory used to cache the downloaded JSON.

    Returns:
        List of record dicts.
    """
    api_key = os.environ.get("FINGERBANK_API_KEY", "").strip()
    cache_path = Path(cache_dir) / _CACHE_FILENAME
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists():
        logger.info("Using cached Fingerbank data: %s", cache_path)
        raw_devices = json.loads(cache_path.read_text(encoding="utf-8"))
    elif not api_key:
        logger.warning(
            "FINGERBANK_API_KEY not set and no cache found — skipping Fingerbank scrape"
        )
        return []
    else:
        logger.info("Fetching Fingerbank device list via API")
        raw_devices = _fetch_all_devices(api_key)
        cache_path.write_text(
            json.dumps(raw_devices, indent=2), encoding="utf-8"
        )
        logger.info("Cached %d Fingerbank devices to %s", len(raw_devices), cache_path)

    records = parse_devices(raw_devices)
    logger.info("Fingerbank: parsed %d categorisable records", len(records))
    return records
