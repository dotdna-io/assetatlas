# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Normalizer for the device knowledge scraping pipeline.

Takes combined output from all 7 scrapers and produces four output CSVs:
  - manufacturer_aliases.csv  — regex alias patterns for manufacturer names
  - manufacturers.csv         — single-category manufacturer entries
  - product_lines.csv         — model-level entries with regex patterns
  - oui_categories.csv        — IEEE OUI prefix → category mappings

Usage:
    from data_scrapers.device_knowledge.normalize import normalize_and_write

    normalize_and_write(all_results, "/path/to/output_dir")
"""

from __future__ import annotations

import csv
import logging
import os
import re
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# Source priority for conflict resolution (higher index = higher priority)
_SOURCE_PRIORITY: dict[str, int] = {
    "cpe": 0,
    "nmap": 1,
    "fusioninventory": 2,
    "netbox": 3,
    "fingerbank": 4,
    # hwdata and ieee_oui contribute aliases/OUI only, not category data
    "hwdata": -1,
    "ieee_oui": -1,
}

# Vendor name suffixes to strip during normalization (shared pattern)
_VENDOR_SUFFIXES = re.compile(
    r"\b(corp\.?|corporation|inc\.?|incorporated|ltd\.?|limited|llc\.?|"
    r"co\.?|gmbh|s\.?a\.?|ag|bv|nv|plc\.?|technologies|technology|"
    r"systems|solutions|networks|electronics|semiconductor|"
    r"international|group|holdings)\b",
    re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    """Lowercase, strip legal suffixes, collapse whitespace."""
    name = name.strip().lower()
    name = _VENDOR_SUFFIXES.sub("", name)
    name = re.sub(r"[,./\\]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _source_priority(source: str) -> int:
    """Return numeric priority for a source name (higher = more trusted)."""
    return _SOURCE_PRIORITY.get(source.lower(), 0)


def _model_to_regex(model: str) -> str:
    """Convert a model name to a regex prefix pattern.

    Examples:
      "PowerEdge R740"  -> "^poweredge"
      "ASR 1001-X"      -> "^asr"
      "2930F-24G Switch" -> "^2930f"
    """
    if not model:
        return ""
    # Take the first significant token (skip leading digits-only tokens
    # if a meaningful word follows, otherwise use the first token)
    tokens = model.strip().lower().split()
    if not tokens:
        return ""
    first = re.sub(r"[^a-z0-9]", "", tokens[0])
    if not first:
        return ""
    return f"^{re.escape(first)}"


# ---------------------------------------------------------------------------
# Build manufacturer_aliases.csv from hwdata entries
# ---------------------------------------------------------------------------

def _build_aliases(all_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build alias records from hwdata scraper output.

    Groups raw vendor names by their normalized form and generates a single
    regex pattern per group that matches any of the raw variants.

    Returns:
        List of dicts with keys: manufacturer_normalized, alias_pattern, raw_names
    """
    # Group raw names by normalized name — only hwdata has vendor name variations
    hwdata_records = [r for r in all_results if r.get("source") == "hwdata"]

    # Also collect all unique manufacturer names from other scrapers
    # so we can build aliases for known name variations across sources
    all_mfr_records = [
        r for r in all_results
        if r.get("manufacturer") and r.get("source") not in ("hwdata", "ieee_oui")
    ]

    # Map normalized -> set of raw names seen (from hwdata raw names)
    norm_to_raws: dict[str, set[str]] = defaultdict(set)

    for rec in hwdata_records:
        raw_name = rec.get("manufacturer", "").strip()
        if not raw_name:
            continue
        norm = _normalize_name(raw_name)
        if norm:
            norm_to_raws[norm].add(raw_name)

    # Also seed with manufacturer names from other scrapers
    for rec in all_mfr_records:
        raw_name = rec.get("manufacturer", "").strip()
        if not raw_name:
            continue
        norm = _normalize_name(raw_name)
        if norm:
            norm_to_raws[norm].add(raw_name)

    aliases = []
    for norm, raws in sorted(norm_to_raws.items()):
        if not norm:
            continue
        # Build a regex that matches the normalized form and any raw variants
        # Use word-boundary anchored pattern on the normalized name as primary
        pattern = re.escape(norm)
        aliases.append({
            "manufacturer_normalized": norm,
            "alias_pattern": pattern,
            "raw_names": "|".join(sorted(raws)),
        })

    logger.info("Built %d manufacturer aliases", len(aliases))
    return aliases


# ---------------------------------------------------------------------------
# Deduplicate and classify manufacturers
# ---------------------------------------------------------------------------

def _build_manufacturer_entries(
    all_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Deduplicate manufacturer entries and separate unambiguous from ambiguous.

    Logic:
    - Group by (manufacturer_normalized, category).
    - Within each group, keep the highest-confidence entry (tie-break: source
      priority).
    - If a manufacturer appears in 2+ distinct categories, it is ambiguous:
      excluded from manufacturers.csv but its product lines are kept.

    Returns:
        (manufacturer_rows, ambiguous_rows, ambiguous_names_set)

        manufacturer_rows — list of dicts for manufacturers.csv
        ambiguous_rows    — list of dicts for manufacturers that are ambiguous
                            (informational only, not written to manufacturers.csv)
        ambiguous_names_set — set of normalized manufacturer names that are
                              ambiguous
    """
    # Only records that carry category data are relevant here
    categorized = [
        r for r in all_results
        if r.get("manufacturer") and r.get("category")
        and r.get("source") not in ("hwdata", "ieee_oui")
    ]

    # Group by (manufacturer_normalized, category) -> best entry
    # key: (mfr_norm, category) -> best record
    best: dict[tuple[str, str], dict[str, Any]] = {}

    for rec in categorized:
        mfr_norm = _normalize_name(rec["manufacturer"])
        if not mfr_norm:
            continue
        category = rec["category"].strip().lower()
        subcategory = rec.get("subcategory", "unknown").strip().lower() or "unknown"
        confidence = float(rec.get("confidence", 0.80))
        source = rec.get("source", "")

        key = (mfr_norm, category)
        existing = best.get(key)
        if existing is None:
            best[key] = {
                "manufacturer_normalized": mfr_norm,
                "category": category,
                "subcategory_default": subcategory,
                "confidence": confidence,
                "source": source,
            }
        else:
            # Replace if higher confidence, or same confidence but higher priority source
            existing_priority = _source_priority(existing["source"])
            new_priority = _source_priority(source)
            if confidence > existing["confidence"] or (
                confidence == existing["confidence"] and new_priority > existing_priority
            ):
                best[key] = {
                    "manufacturer_normalized": mfr_norm,
                    "category": category,
                    "subcategory_default": subcategory,
                    "confidence": confidence,
                    "source": source,
                }

    # Now detect ambiguous manufacturers (appear in 2+ distinct categories)
    mfr_categories: dict[str, set[str]] = defaultdict(set)
    for (mfr_norm, category) in best:
        mfr_categories[mfr_norm].add(category)

    ambiguous_names: set[str] = {
        mfr for mfr, cats in mfr_categories.items() if len(cats) >= 2
    }

    manufacturer_rows = []
    ambiguous_rows = []

    for (mfr_norm, category), entry in sorted(best.items()):
        if mfr_norm in ambiguous_names:
            ambiguous_rows.append(entry)
        else:
            manufacturer_rows.append(entry)

    if ambiguous_names:
        logger.info(
            "Ambiguous manufacturers (excluded from manufacturers.csv): %s",
            sorted(ambiguous_names),
        )

    logger.info(
        "Manufacturer entries: %d unambiguous, %d ambiguous",
        len(manufacturer_rows),
        len(ambiguous_rows),
    )
    return manufacturer_rows, ambiguous_rows, ambiguous_names


# ---------------------------------------------------------------------------
# Build product_lines.csv
# ---------------------------------------------------------------------------

def _build_product_lines(all_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build model-level product line entries.

    Only records that have both a manufacturer and a model (non-empty) and a
    category are included.

    For each (manufacturer, model_pattern) pair, keep the highest-confidence
    entry. Generate regex patterns from model names.

    Returns:
        List of dicts for product_lines.csv.
    """
    categorized_with_model = [
        r for r in all_results
        if r.get("manufacturer") and r.get("model") and r.get("category")
        and r.get("source") not in ("hwdata", "ieee_oui")
    ]

    # key: (mfr_norm, model_pattern) -> best record
    best: dict[tuple[str, str], dict[str, Any]] = {}

    for rec in categorized_with_model:
        mfr_norm = _normalize_name(rec["manufacturer"])
        if not mfr_norm:
            continue
        model = rec["model"].strip()
        pattern = _model_to_regex(model)
        if not pattern:
            continue

        category = rec["category"].strip().lower()
        subcategory = rec.get("subcategory", "unknown").strip().lower() or "unknown"
        confidence = float(rec.get("confidence", 0.90))
        source = rec.get("source", "")

        key = (mfr_norm, pattern)
        existing = best.get(key)
        if existing is None:
            best[key] = {
                "manufacturer_normalized": mfr_norm,
                "model_pattern": pattern,
                "category": category,
                "subcategory": subcategory,
                "confidence": confidence,
                "source": source,
            }
        else:
            existing_priority = _source_priority(existing["source"])
            new_priority = _source_priority(source)
            if confidence > existing["confidence"] or (
                confidence == existing["confidence"] and new_priority > existing_priority
            ):
                best[key] = {
                    "manufacturer_normalized": mfr_norm,
                    "model_pattern": pattern,
                    "category": category,
                    "subcategory": subcategory,
                    "confidence": confidence,
                    "source": source,
                }

    rows = sorted(best.values(), key=lambda r: (r["manufacturer_normalized"], r["model_pattern"]))
    logger.info("Built %d product line entries", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Build oui_categories.csv
# ---------------------------------------------------------------------------

def _build_oui_categories(
    all_results: list[dict[str, Any]],
    manufacturers: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Build OUI → category mapping.

    Joins IEEE OUI records with manufacturer category data.

    Args:
        all_results: All scraper results.
        manufacturers: Dict of {mfr_norm: (category, subcategory)} from the
            unambiguous manufacturers table.

    Returns:
        List of dicts for oui_categories.csv.
    """
    oui_records = [r for r in all_results if r.get("source") == "ieee_oui"]

    rows = []
    seen_prefixes: set[str] = set()

    for rec in oui_records:
        mac_prefix = rec.get("mac_prefix", "").strip().upper()
        mfr_raw = rec.get("manufacturer", "").strip()
        if not mac_prefix or not mfr_raw:
            continue
        if mac_prefix in seen_prefixes:
            continue

        mfr_norm = _normalize_name(mfr_raw)
        if not mfr_norm:
            continue

        # Find category from unambiguous manufacturer table
        cat_info = manufacturers.get(mfr_norm)
        if cat_info:
            category = cat_info[0]
        else:
            # No known category for this OUI vendor — still emit the row
            # with empty category so the OUI→vendor mapping is preserved
            category = ""

        seen_prefixes.add(mac_prefix)
        rows.append({
            "oui_prefix": mac_prefix,
            "manufacturer_normalized": mfr_norm,
            "category": category,
            "source": "ieee_oui",
        })

    logger.info("Built %d OUI category entries", len(rows))
    return rows


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def _write_csv(path: str, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize_and_write(all_results: list[dict[str, Any]], output_dir: str) -> None:
    """Normalize all scraper results and write four output CSVs.

    Args:
        all_results: Combined list of dicts from all scrapers.
        output_dir: Directory where the four CSV files will be written.
    """
    os.makedirs(output_dir, exist_ok=True)

    # 1. Build manufacturer aliases (from hwdata + all source names)
    alias_rows = _build_aliases(all_results)
    _write_csv(
        os.path.join(output_dir, "manufacturer_aliases.csv"),
        fieldnames=["manufacturer_normalized", "alias_pattern", "raw_names"],
        rows=alias_rows,
    )

    # 2. Deduplicate manufacturers
    manufacturer_rows, _ambiguous_rows, ambiguous_names = _build_manufacturer_entries(all_results)

    # 3. Write manufacturers.csv (unambiguous only)
    _write_csv(
        os.path.join(output_dir, "manufacturers.csv"),
        fieldnames=["manufacturer_normalized", "category", "subcategory_default", "confidence", "source"],
        rows=manufacturer_rows,
    )

    # 4. Build and write product_lines.csv
    product_line_rows = _build_product_lines(all_results)
    _write_csv(
        os.path.join(output_dir, "product_lines.csv"),
        fieldnames=["manufacturer_normalized", "model_pattern", "category", "subcategory", "confidence", "source"],
        rows=product_line_rows,
    )

    # 5. Build OUI categories (join OUI with manufacturer data)
    mfr_lookup: dict[str, tuple[str, str]] = {
        row["manufacturer_normalized"]: (row["category"], row.get("subcategory_default", "unknown"))
        for row in manufacturer_rows
    }
    oui_rows = _build_oui_categories(all_results, mfr_lookup)
    _write_csv(
        os.path.join(output_dir, "oui_categories.csv"),
        fieldnames=["oui_prefix", "manufacturer_normalized", "category", "source"],
        rows=oui_rows,
    )

    print(
        f"normalize_and_write complete: "
        f"{len(alias_rows)} aliases, "
        f"{len(manufacturer_rows)} manufacturers "
        f"({len(ambiguous_names)} ambiguous excluded), "
        f"{len(product_line_rows)} product lines, "
        f"{len(oui_rows)} OUI entries"
    )
