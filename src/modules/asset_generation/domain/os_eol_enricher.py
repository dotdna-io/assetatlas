# SPDX-License-Identifier: AGPL-3.0-or-later
"""
OS End-of-Life Enricher

Enriches assets with operating system end-of-life information.

This enricher adds EOL metadata to assets after OS normalization has occurred.
It queries the os_eol_data database table to find EOL dates and calculates
EOL status based on current date.

Key responsibilities:
- Look up EOL data for normalized OS names and versions
- Calculate EOL status (active, approaching_eol, end_of_life, unknown)
- Add EOL fields to assets (eol_date, eol_status, support_end_date, etc.)
- Cache lookups for performance
"""

import logging
from datetime import date, datetime
from typing import Optional, Dict, Any
from functools import lru_cache

import pandas as pd

from src.modules.asset_generation.services.eol_data_service import EOLDataService, EOLData

logger = logging.getLogger(__name__)


# EOL status threshold (days before EOL to mark as "approaching")
EOL_APPROACHING_THRESHOLD_DAYS = 180  # 6 months


class OSEOLEnricher:
    """
    Enriches assets with OS End-of-Life information.

    This enricher operates on assets that have already been through OS normalization.
    It adds EOL-related fields to each asset based on the normalized OS name and version.
    """

    def __init__(self, eol_service: EOLDataService):
        """
        Initialize OS EOL Enricher.

        Args:
            eol_service: EOL data service for database lookups
        """
        self.eol_service = eol_service
        self._cache = {}  # In-memory cache for performance
        self._cache_hits = 0
        self._cache_misses = 0
        self._enrichment_stats = {
            'total_processed': 0,
            'with_eol_data': 0,
            'without_eol_data': 0,
            'active': 0,
            'approaching_eol': 0,
            'end_of_life': 0,
            'unknown': 0,
            'enrichment_failed': 0
        }

        logger.info("OSEOLEnricher initialized")

    # =========================================================================
    # EOL Status Calculation
    # =========================================================================

    def calculate_eol_status(self, eol_date: Optional[date]) -> str:
        """
        Calculate EOL status based on EOL date.

        Args:
            eol_date: End-of-life date

        Returns:
            EOL status: 'active', 'approaching_eol', 'end_of_life', or 'unknown'
        """
        if not eol_date:
            return 'unknown'

        today = date.today()
        days_until_eol = (eol_date - today).days

        if days_until_eol < 0:
            return 'end_of_life'
        elif days_until_eol <= EOL_APPROACHING_THRESHOLD_DAYS:
            return 'approaching_eol'
        else:
            return 'active'

    # =========================================================================
    # EOL Data Lookup
    # =========================================================================

    def _lookup_eol_with_cache(self, os_name: str, os_version: Optional[str]) -> Optional[EOLData]:
        """
        Look up EOL data with caching.

        Args:
            os_name: Normalized OS name
            os_version: OS version string

        Returns:
            EOLData object or None
        """
        cache_key = f"{os_name}|{os_version}"

        # Check cache
        if cache_key in self._cache:
            self._cache_hits += 1
            return self._cache[cache_key]

        # Cache miss - lookup from database
        self._cache_misses += 1
        eol_data = self.eol_service.lookup_eol_data(os_name, os_version)

        # Cache the result (including None)
        self._cache[cache_key] = eol_data

        return eol_data

    # =========================================================================
    # Asset Enrichment
    # =========================================================================

    def enrich_asset(self, asset: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich a single asset with EOL information.

        Args:
            asset: Asset dictionary (must have os_normalized field)

        Returns:
            Enriched asset dictionary with EOL fields added
        """
        self._enrichment_stats['total_processed'] += 1

        # Extract OS information
        os_name = asset.get('os_normalized')
        # A missing os_normalized can arrive as float('nan') (not None) when the column
        # holds np.nan or was realigned via reindex/merge — enrich_dataframe's
        # df.to_dict('records') preserves NaN, which is truthy. Coerce it to None so the
        # no-name bail below catches it; otherwise NaN reaches map_os_to_product, which
        # crashes on nan.lower() and enrich_os_eol's broad except then wipes EOL data for
        # the whole batch (#681 A10).
        if isinstance(os_name, float) and pd.isna(os_name):
            os_name = None

        # Try to extract version from os_enrichment JSONB field
        os_version = None
        os_enrichment = asset.get('os_enrichment')
        if os_enrichment and isinstance(os_enrichment, dict):
            os_version = os_enrichment.get('version')

        # Fallback: try to extract version from os_normalized string
        if not os_version and os_name:
            # Parse version from os_normalized if it contains version info
            # Example: "Windows 10 21H2" -> version = "21H2"
            parts = str(os_name).split()
            if len(parts) > 1:
                # Last part might be version
                potential_version = parts[-1]
                # Check if it looks like a version (contains digit)
                if any(c.isdigit() for c in potential_version):
                    os_version = potential_version

        # Bail to unknown only when there's no OS name at all to look up. os_version may be
        # None: lookup_eol_data resolves Windows Server / iOS / Android (version embedded in
        # os_name) from os_name alone, so don't pre-empt the lookup just because os_normalized's
        # last token carried no digit (#681 A10).
        if not os_name:
            asset['os_eol_status'] = 'unknown'
            asset['os_eol_date'] = None
            asset['os_support_end_date'] = None
            asset['os_extended_support_date'] = None
            asset['os_is_lts'] = None
            asset['os_eol_checked_at'] = datetime.now()
            self._enrichment_stats['unknown'] += 1
            self._enrichment_stats['without_eol_data'] += 1
            return asset

        # Lookup EOL data (os_version may be None — the service resolves from os_name where the
        # version is embedded in the name)
        eol_data = self._lookup_eol_with_cache(os_name, os_version)

        if eol_data:
            # Check data freshness (#64)
            if eol_data.last_synced:
                from datetime import timezone
                age_days = (datetime.now(timezone.utc) - eol_data.last_synced.replace(
                    tzinfo=eol_data.last_synced.tzinfo or timezone.utc
                )).days
                if age_days > 90:
                    logger.warning(
                        f"Stale EOL data for {os_name} {os_version} "
                        f"(last synced {age_days} days ago)"
                    )

            # Calculate EOL status
            eol_status = self.calculate_eol_status(eol_data.eol_date)

            # Add EOL fields to asset
            asset['os_eol_date'] = eol_data.eol_date
            asset['os_support_end_date'] = eol_data.support_end_date
            asset['os_extended_support_date'] = eol_data.extended_support_date
            asset['os_is_lts'] = eol_data.lts
            asset['os_eol_status'] = eol_status
            asset['os_eol_checked_at'] = datetime.now()

            # Update stats
            self._enrichment_stats['with_eol_data'] += 1
            self._enrichment_stats[eol_status] += 1

            logger.debug(
                f"Enriched asset with EOL data: os={os_name} {os_version}, "
                f"status={eol_status}, eol_date={eol_data.eol_date}"
            )
        else:
            # No EOL data found
            asset['os_eol_status'] = 'unknown'
            asset['os_eol_date'] = None
            asset['os_support_end_date'] = None
            asset['os_extended_support_date'] = None
            asset['os_is_lts'] = None
            asset['os_eol_checked_at'] = datetime.now()

            self._enrichment_stats['unknown'] += 1
            self._enrichment_stats['without_eol_data'] += 1

            logger.debug(f"No EOL data found for: os={os_name}, version={os_version}")

        return asset

    def enrich_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Enrich a DataFrame of assets with EOL information.

        Args:
            df: DataFrame with assets (must have os_normalized column)

        Returns:
            Enriched DataFrame with EOL columns added
        """
        import time
        start_time = time.time()

        logger.info(f"Enriching {len(df)} assets with EOL information...")

        # Reset stats for this run
        self._enrichment_stats = {
            'total_processed': 0,
            'with_eol_data': 0,
            'without_eol_data': 0,
            'active': 0,
            'approaching_eol': 0,
            'end_of_life': 0,
            'unknown': 0,
            'enrichment_failed': 0
        }

        # Convert DataFrame to list of dicts, enrich, and convert back
        assets = df.to_dict('records')
        enriched_assets = [self.enrich_asset(asset) for asset in assets]
        enriched_df = pd.DataFrame(enriched_assets)

        elapsed = time.time() - start_time

        # Log statistics
        logger.info(
            f"EOL enrichment completed in {elapsed:.2f}s "
            f"({len(df)/elapsed:.0f} assets/sec)"
        )
        logger.info(f"EOL enrichment stats: {self._enrichment_stats}")
        logger.info(
            f"Cache performance: {self._cache_hits} hits, {self._cache_misses} misses "
            f"({self._cache_hits/(self._cache_hits+self._cache_misses)*100:.1f}% hit rate)"
        )

        return enriched_df

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_enrichment_stats(self) -> Dict[str, Any]:
        """
        Get enrichment statistics.

        Returns:
            Dictionary with enrichment stats
        """
        total = self._enrichment_stats['total_processed']
        if total == 0:
            return self._enrichment_stats

        return {
            **self._enrichment_stats,
            'enrichment_rate': self._enrichment_stats['with_eol_data'] / total * 100,
            'cache_size': len(self._cache),
            'cache_hits': self._cache_hits,
            'cache_misses': self._cache_misses,
            'cache_hit_rate': self._cache_hits / (self._cache_hits + self._cache_misses) * 100
                             if (self._cache_hits + self._cache_misses) > 0 else 0
        }

    def clear_cache(self) -> None:
        """Clear the EOL data cache."""
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0
        logger.info("EOL enrichment cache cleared")

    def get_cache_size(self) -> int:
        """Get current cache size."""
        return len(self._cache)

    def get_eol_status_distribution(self) -> Dict[str, int]:
        """
        Get distribution of EOL statuses from last enrichment run.

        Returns:
            Dictionary with status counts
        """
        return {
            'active': self._enrichment_stats['active'],
            'approaching_eol': self._enrichment_stats['approaching_eol'],
            'end_of_life': self._enrichment_stats['end_of_life'],
            'unknown': self._enrichment_stats['unknown'],
            'enrichment_failed': self._enrichment_stats.get('enrichment_failed', 0)
        }

    def pre_warm_cache(self, os_list: list[tuple[str, str]]) -> None:
        """
        Pre-warm cache with commonly seen OS/version combinations.

        Args:
            os_list: List of (os_name, os_version) tuples to pre-load
        """
        logger.info(f"Pre-warming EOL cache with {len(os_list)} OS/version combinations")

        for os_name, os_version in os_list:
            self._lookup_eol_with_cache(os_name, os_version)

        logger.info(f"Cache pre-warmed. Cache size: {len(self._cache)}")
