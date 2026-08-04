# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Device Knowledge Base — Tier 2 of the categorization cascade.

Loads CSV reference data from disk at pipeline startup and provides fast
lookups for assets that rule-based prediction (Tier 1) couldn't resolve.

Lookup chain:
  1. Normalize manufacturer via alias table
  2. Product-line match (manufacturer + model regex) → confidence 0.90
  3. Manufacturer-only match (non-ambiguous) → confidence from CSV (default 0.80)
  4. OUI fallback via mac_vendor → confidence 0.75
  5. No match → None
"""

import csv
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

# Common corporate suffixes stripped during manufacturer normalization
_CORP_SUFFIX_RE = re.compile(
    r",?\s*\b(inc\.?|corp\.?|co\.?|ltd\.?|llc|gmbh|s\.?a\.?|plc)\s*$",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


class DeviceKnowledgeBase:
    """
    CSV-backed device knowledge base for category/subcategory lookups.

    Pure domain class — no DB access, no Flask context, no pipeline
    dependencies. Reads CSV files once at init and answers lookups in memory.
    """

    def __init__(self, data_dir: str = "data/device_knowledge_base"):
        self._aliases: List[Tuple[re.Pattern, str]] = []
        self._product_lines: Dict[str, List[Tuple[re.Pattern, str, str, float]]] = {}
        self._manufacturers: Dict[str, Tuple[str, str, float]] = {}
        self._oui: Dict[str, Tuple[str, str]] = {}
        self._ambiguous_manufacturers: set = set()
        self._normalize_cache: Dict[str, str] = {}
        self._model_cache: Dict[str, bool] = {}

        if not os.path.isdir(data_dir):
            logger.warning("Device knowledge base directory not found: %s", data_dir)
            return

        self._aliases = self._load_aliases(
            os.path.join(data_dir, "manufacturer_aliases.csv")
        )
        self._product_lines = self._load_product_lines(
            os.path.join(data_dir, "product_lines.csv")
        )
        self._manufacturers = self._load_manufacturers(
            os.path.join(data_dir, "manufacturers.csv")
        )
        self._oui = self._load_oui(
            os.path.join(data_dir, "oui_categories.csv")
        )
        self._ambiguous_manufacturers = self._detect_ambiguous_manufacturers()

        logger.info(
            "DeviceKnowledgeBase loaded: %d aliases, %d product-line groups, "
            "%d manufacturers, %d OUI prefixes, %d ambiguous manufacturers",
            len(self._aliases),
            len(self._product_lines),
            len(self._manufacturers),
            len(self._oui),
            len(self._ambiguous_manufacturers),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(
        self, manufacturer: str, model: str, mac_vendor: str
    ) -> Optional[Tuple[str, str, float]]:
        """
        Look up category, subcategory, and confidence for a device.

        Args:
            manufacturer: Raw manufacturer string (e.g. "Dell Inc")
            model: Raw model string (e.g. "PowerEdge R740")
            mac_vendor: MAC OUI vendor string (e.g. "VMware, Inc.")

        Returns:
            (category, subcategory, confidence) or None if no match.
        """
        mfr_norm = self._normalize_manufacturer(manufacturer)
        model_lower = model.strip().lower() if model else ""

        # Step 1: Product-line match (manufacturer + model regex)
        if mfr_norm and model_lower and mfr_norm in self._product_lines:
            for pattern, category, subcategory, confidence in self._product_lines[mfr_norm]:
                if pattern.search(model_lower):
                    return (category, subcategory, confidence)

        # Step 2: Manufacturer-only match (skip ambiguous)
        if mfr_norm and mfr_norm not in self._ambiguous_manufacturers:
            if mfr_norm in self._manufacturers:
                cat, subcat, conf = self._manufacturers[mfr_norm]
                return (cat, subcat, conf)

        # Step 3: OUI fallback via mac_vendor
        if mac_vendor:
            mac_vendor_norm = self._normalize_manufacturer(mac_vendor)
            if mac_vendor_norm and mac_vendor_norm not in self._ambiguous_manufacturers:
                if mac_vendor_norm in self._manufacturers:
                    cat, subcat, conf = self._manufacturers[mac_vendor_norm]
                    return (cat, subcat, 0.75)

        return None

    # Maximum hostname length to consider as a potential model name.
    # Prevents long hostnames like "enterprise-server-01" (19 chars)
    # from matching short model prefixes like "^enterprise".
    _MODEL_HOSTNAME_MAX_LENGTH = 12

    def is_known_model(self, value: str) -> bool:
        """
        Check if a value matches a known device model name.

        Used by hostname validation to detect model numbers being used
        as hostnames (e.g., "MP56-E2", "VVX500").

        Args:
            value: String to check (typically a hostname)

        Returns:
            True if the value matches a known product line model pattern
            and is short enough to be a bare model name.
        """
        if not value:
            return False

        value_lower = value.strip().lower()
        if not value_lower:
            return False

        if value_lower in self._model_cache:
            return self._model_cache[value_lower]

        result = False
        if len(value_lower) <= self._MODEL_HOSTNAME_MAX_LENGTH:
            for patterns in self._product_lines.values():
                for pattern, _cat, _subcat, _conf in patterns:
                    if pattern.search(value_lower):
                        result = True
                        break
                if result:
                    break

        self._model_cache[value_lower] = result
        return result

    # ------------------------------------------------------------------
    # CSV loaders
    # ------------------------------------------------------------------

    def _load_aliases(self, path: str) -> List[Tuple[re.Pattern, str]]:
        """Load manufacturer_aliases.csv and compile each alias_pattern."""
        if not os.path.isfile(path):
            logger.debug("Alias file not found: %s", path)
            return []

        aliases = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    pattern = re.compile(row["alias_pattern"].strip(), re.IGNORECASE)
                    normalized = row["manufacturer_normalized"].strip().lower()
                    aliases.append((pattern, normalized))
                except re.error as e:
                    logger.warning("Bad alias regex %r: %s", row.get("alias_pattern"), e)

        logger.debug("Loaded %d manufacturer aliases from %s", len(aliases), path)
        return aliases

    def _load_product_lines(
        self, path: str
    ) -> Dict[str, List[Tuple[re.Pattern, str, str, float]]]:
        """Load product_lines.csv, compile model_pattern, group by manufacturer."""
        if not os.path.isfile(path):
            logger.debug("Product lines file not found: %s", path)
            return {}

        product_lines: Dict[str, List[Tuple[re.Pattern, str, str, float]]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    mfr = row["manufacturer_normalized"].strip().lower()
                    pattern = re.compile(row["model_pattern"].strip(), re.IGNORECASE)
                    category = row["category"].strip().lower()
                    subcategory = row["subcategory"].strip().lower()
                    confidence = float(row["confidence"].strip())
                    product_lines.setdefault(mfr, []).append(
                        (pattern, category, subcategory, confidence)
                    )
                except (re.error, ValueError, KeyError) as e:
                    logger.warning("Bad product line row %r: %s", row, e)

        logger.debug("Loaded product lines for %d manufacturers from %s", len(product_lines), path)
        return product_lines

    def _load_manufacturers(self, path: str) -> Dict[str, Tuple[str, str, float]]:
        """Load manufacturers.csv into dict keyed by manufacturer_normalized."""
        if not os.path.isfile(path):
            logger.debug("Manufacturers file not found: %s", path)
            return {}

        manufacturers: Dict[str, Tuple[str, str, float]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    mfr = row["manufacturer_normalized"].strip().lower()
                    category = row["category"].strip().lower()
                    subcategory = row["subcategory_default"].strip().lower()
                    confidence = float(row["confidence"].strip())
                    manufacturers[mfr] = (category, subcategory, confidence)
                except (ValueError, KeyError) as e:
                    logger.warning("Bad manufacturer row %r: %s", row, e)

        logger.debug("Loaded %d manufacturers from %s", len(manufacturers), path)
        return manufacturers

    def _load_oui(self, path: str) -> Dict[str, Tuple[str, str]]:
        """Load oui_categories.csv into dict keyed by OUI prefix."""
        if not os.path.isfile(path):
            logger.debug("OUI file not found: %s", path)
            return {}

        oui: Dict[str, Tuple[str, str]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    prefix = row["oui_prefix"].strip().upper()
                    mfr = row["manufacturer_normalized"].strip().lower()
                    category = row["category"].strip().lower()
                    oui[prefix] = (mfr, category)
                except KeyError as e:
                    logger.warning("Bad OUI row %r: %s", row, e)

        logger.debug("Loaded %d OUI prefixes from %s", len(oui), path)
        return oui

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_ambiguous_manufacturers(self) -> set:
        """
        Find manufacturers that appear in 2+ distinct categories across
        product_lines. These should not be used for manufacturer-only fallback.
        """
        categories_per_mfr: Dict[str, set] = {}
        for mfr, lines in self._product_lines.items():
            cats = {cat for _, cat, _, _ in lines}
            categories_per_mfr[mfr] = cats

        ambiguous = {mfr for mfr, cats in categories_per_mfr.items() if len(cats) >= 2}
        if ambiguous:
            logger.debug("Ambiguous manufacturers (2+ categories): %s", ambiguous)
        return ambiguous

    def _normalize_manufacturer(self, name: str) -> str:
        """
        Lowercase + strip + remove corporate suffixes, then try each alias
        regex. Return first match's normalized name, or the cleaned input
        if no alias matches. Results are cached since manufacturer names
        repeat heavily across assets.
        """
        if not name:
            return ""

        lowered = name.strip().lower()

        # Check cache first (manufacturer names repeat heavily across assets)
        if lowered in self._normalize_cache:
            return self._normalize_cache[lowered]

        result = self._normalize_manufacturer_uncached(lowered)
        self._normalize_cache[lowered] = result
        return result

    def _normalize_manufacturer_uncached(self, lowered: str) -> str:
        """Uncached normalization — alias regex scan + suffix stripping."""
        # Try alias match on raw lowercased input first
        for pattern, normalized in self._aliases:
            if pattern.search(lowered):
                return normalized

        # Strip corporate suffixes and try aliases again
        cleaned = _CORP_SUFFIX_RE.sub("", lowered).strip()
        if cleaned != lowered:
            for pattern, normalized in self._aliases:
                if pattern.search(cleaned):
                    return normalized

        return cleaned
