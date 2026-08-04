# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Conflict Detector — identifies business-level data quality conflicts.

Analyzes provider-level data to detect:
- OS family conflicts (Windows + macOS on same asset)
- Manufacturer family conflicts (Dell + Lenovo on same asset)
- Excessive provider count (>30, suggesting over-merge)
- Excessive hostname count (>3 distinct, suggesting over-merge)
"""

import logging
import re
from typing import Dict, List, Optional
from uuid import UUID

import pandas as pd

from src.modules.asset_generation.domain.manufacturer_normalizer import normalize_manufacturer

logger = logging.getLogger(__name__)


class ConflictDetector:
    """Detects business-level data quality conflicts in asset providers."""

    EXCESSIVE_PROVIDER_THRESHOLD = 30
    EXCESSIVE_HOSTNAME_THRESHOLD = 3

    # OS families that should never coexist on the same asset
    _OS_FAMILY_PATTERNS = {
        'windows': re.compile(r'^windows', re.IGNORECASE),
        'macos': re.compile(r'^macos', re.IGNORECASE),
        'apple_ios': re.compile(r'^(?:apple\s+)?ios(?:\s|$)', re.IGNORECASE),
        'android': re.compile(r'^android', re.IGNORECASE),
    }
    _CONFLICTING_OS_PAIRS = frozenset({
        ('windows', 'macos'),
        ('windows', 'apple_ios'),
        ('macos', 'android'),
    })

    # Manufacturer families — uses canonical names from manufacturer_normalizer
    _CONFLICTING_MFR_FAMILIES = {'dell', 'lenovo', 'hp', 'apple'}
    _CONFLICTING_MFR_PAIRS = frozenset({
        ('dell', 'lenovo'), ('dell', 'hp'), ('dell', 'apple'),
        ('lenovo', 'hp'), ('lenovo', 'apple'), ('hp', 'apple'),
    })

    def __init__(self, generation_run_id: UUID):
        self.generation_run_id = generation_run_id

    def detect_conflicts(self, providers_df: pd.DataFrame) -> Dict:
        """Detect all business-level conflicts from provider data.

        Args:
            providers_df: DataFrame with columns including asset_id,
                         os_normalized, manufacturer, hostname, data_source

        Returns:
            Dictionary with conflict statistics and details
        """
        logger.info("Starting business-level conflict detection...")

        findings = {
            'os_family_conflicts': self._detect_os_family_conflicts(providers_df),
            'manufacturer_conflicts': self._detect_manufacturer_conflicts(providers_df),
            'excessive_providers': self._detect_excessive_providers(providers_df),
            'excessive_hostnames': self._detect_excessive_hostnames(providers_df),
        }

        total = sum(f['count'] for f in findings.values())
        findings['total_conflicts'] = total
        findings['has_critical'] = findings['os_family_conflicts']['count'] > 0

        logger.info(
            "Conflict detection complete: %d findings "
            "(OS conflicts: %d, mfr conflicts: %d, "
            "excessive providers: %d, excessive hostnames: %d)",
            total,
            findings['os_family_conflicts']['count'],
            findings['manufacturer_conflicts']['count'],
            findings['excessive_providers']['count'],
            findings['excessive_hostnames']['count'],
        )

        return findings

    @classmethod
    def _classify_os_family(cls, os_value: str) -> Optional[str]:
        """Classify an OS string into a family, or None if unrecognized."""
        if not isinstance(os_value, str):
            return None
        for family, pattern in cls._OS_FAMILY_PATTERNS.items():
            if pattern.search(os_value):
                return family
        return None

    @classmethod
    def _classify_mfr_family(cls, mfr_value: str) -> Optional[str]:
        """Classify a manufacturer string into a family using the canonical normalizer."""
        if not isinstance(mfr_value, str):
            return None
        canonical = normalize_manufacturer(mfr_value)
        if canonical:
            key = canonical.lower()
            if key in cls._CONFLICTING_MFR_FAMILIES:
                return key
        return None

    def _detect_os_family_conflicts(self, df: pd.DataFrame) -> Dict:
        """Detect assets with conflicting OS families across providers."""
        result = {'count': 0, 'asset_ids': []}

        if 'os_normalized' not in df.columns or 'asset_id' not in df.columns:
            return result

        # Filter to rows with non-null OS, classify into families
        os_df = df[['asset_id', 'os_normalized']].dropna(subset=['os_normalized']).copy()
        if os_df.empty:
            return result

        os_df['os_family'] = os_df['os_normalized'].map(self._classify_os_family)
        os_df = os_df.dropna(subset=['os_family'])
        if os_df.empty:
            return result

        # Get unique families per asset
        families_per_asset = os_df.groupby('asset_id')['os_family'].apply(
            lambda x: frozenset(x.unique())
        )

        # Check each asset for conflicting pairs
        conflicting_ids = []
        for asset_id, families in families_per_asset.items():
            if len(families) < 2:
                continue
            for pair in self._CONFLICTING_OS_PAIRS:
                if pair[0] in families and pair[1] in families:
                    conflicting_ids.append(asset_id)
                    logger.warning(
                        "OS family conflict on asset %s: %s",
                        asset_id, sorted(families),
                    )
                    break

        result['count'] = len(conflicting_ids)
        result['asset_ids'] = conflicting_ids[:10]
        return result

    def _detect_manufacturer_conflicts(self, df: pd.DataFrame) -> Dict:
        """Detect assets with conflicting manufacturer families across providers."""
        result = {'count': 0, 'asset_ids': []}

        if 'manufacturer' not in df.columns or 'asset_id' not in df.columns:
            return result

        # Filter to rows with non-null manufacturer, classify into families
        mfr_df = df[['asset_id', 'manufacturer']].dropna(subset=['manufacturer']).copy()
        if mfr_df.empty:
            return result

        mfr_df['mfr_family'] = mfr_df['manufacturer'].map(self._classify_mfr_family)
        mfr_df = mfr_df.dropna(subset=['mfr_family'])
        if mfr_df.empty:
            return result

        # Get unique families per asset
        families_per_asset = mfr_df.groupby('asset_id')['mfr_family'].apply(
            lambda x: frozenset(x.unique())
        )

        # Check each asset for conflicting pairs
        conflicting_ids = []
        for asset_id, families in families_per_asset.items():
            if len(families) < 2:
                continue
            for pair in self._CONFLICTING_MFR_PAIRS:
                if pair[0] in families and pair[1] in families:
                    conflicting_ids.append(asset_id)
                    logger.warning(
                        "Manufacturer family conflict on asset %s: %s",
                        asset_id, sorted(families),
                    )
                    break

        result['count'] = len(conflicting_ids)
        result['asset_ids'] = conflicting_ids[:10]
        return result

    def _detect_excessive_providers(self, df: pd.DataFrame) -> Dict:
        """Detect assets with an excessive number of providers."""
        result = {'count': 0, 'asset_ids': [], 'max_providers': 0}

        if 'asset_id' not in df.columns:
            return result

        counts = df.groupby('asset_id').size()
        if counts.empty:
            return result

        result['max_providers'] = int(counts.max())
        excessive = counts[counts > self.EXCESSIVE_PROVIDER_THRESHOLD]

        if not excessive.empty:
            result['count'] = len(excessive)
            result['asset_ids'] = list(excessive.index[:10])
            logger.warning(
                "%d assets exceed %d providers (max: %d)",
                len(excessive), self.EXCESSIVE_PROVIDER_THRESHOLD,
                result['max_providers'],
            )

        return result

    def _detect_excessive_hostnames(self, df: pd.DataFrame) -> Dict:
        """Detect assets with an excessive number of distinct hostnames."""
        result = {'count': 0, 'asset_ids': [], 'max_hostnames': 0}

        if 'hostname' not in df.columns or 'asset_id' not in df.columns:
            return result

        # Count distinct non-null hostnames per asset
        hostname_df = df[['asset_id', 'hostname']].dropna(subset=['hostname'])
        if hostname_df.empty:
            return result

        distinct_counts = hostname_df.groupby('asset_id')['hostname'].nunique()
        if distinct_counts.empty:
            return result

        result['max_hostnames'] = int(distinct_counts.max())
        excessive = distinct_counts[distinct_counts > self.EXCESSIVE_HOSTNAME_THRESHOLD]

        if not excessive.empty:
            result['count'] = len(excessive)
            result['asset_ids'] = list(excessive.index[:10])
            logger.warning(
                "%d assets exceed %d distinct hostnames (max: %d)",
                len(excessive), self.EXCESSIVE_HOSTNAME_THRESHOLD,
                result['max_hostnames'],
            )

        return result
