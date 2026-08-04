# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Repository - Persistence layer for assets.

This module is responsible for:
- Saving and retrieving assets
- Bulk asset operations
- Query operations
"""

import logging
from typing import Dict, List, Optional, Any
from uuid import UUID

import pandas as pd

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class AssetRepository:
    """
    Repository for asset persistence operations.

    Provides high-level interface for storing and retrieving assets.
    """

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize asset repository.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter
        logger.info("AssetRepository initialized")

    def save_assets(self, assets_df: pd.DataFrame, generation_run_id: Optional[UUID] = None, archive_existing: bool = True) -> int:
        """
        Save assets from DataFrame to database.

        Args:
            assets_df: DataFrame with asset data
            generation_run_id: Optional UUID to link this generation run (used for history)
            archive_existing: If True, archive existing assets to history before saving new ones

        Returns:
            Number of assets saved
        """
        logger.info(f"Saving {len(assets_df)} assets to database...")

        # Archive existing assets to history if requested
        if archive_existing:
            try:
                archived_counts = self.db.archive_all_to_history(generation_run_id)
                logger.info(
                    f"Archived existing data to history: {archived_counts['assets']} assets, "
                    f"{archived_counts['providers']} providers"
                )
            except Exception as e:
                logger.error(f"Failed to archive data to history: {e}")
                raise

        # NOTE: Excluded records (id='<NA>') are now filtered out earlier in the pipeline (Stage 4.7)
        # and saved to the excluded_records table. They should not reach this point.
        # This validation check remains as a safety net to catch any unexpected invalid IDs.
        invalid_mask = (assets_df['id'] == '<NA>') | (assets_df['id'].isna()) | (assets_df['id'] == '')
        invalid_count = invalid_mask.sum()

        if invalid_count > 0:
            logger.error(
                f"UNEXPECTED: Found {invalid_count} assets with invalid IDs at save stage. "
                f"These should have been filtered at Stage 4.7. This indicates a pipeline bug."
            )
            # Filter them out to prevent database errors
            assets_df = assets_df[~invalid_mask].copy()

        # Check for duplicate columns and log details
        duplicate_cols = assets_df.columns[assets_df.columns.duplicated()].tolist()
        if duplicate_cols:
            unique_dupes = list(set(duplicate_cols))
            logger.warning(f"Duplicate columns detected: {unique_dupes}")
            logger.warning(f"All columns: {assets_df.columns.tolist()}")
            # De-duplicate by keeping first occurrence
            assets_df = assets_df.loc[:, ~assets_df.columns.duplicated()]
            logger.info(f"De-duplicated columns - now have {len(assets_df.columns)} unique columns")

        # Convert assets DataFrame to list of dicts
        assets_list = assets_df.to_dict('records')

        # Use bulk upsert for all assets
        count = self.db.bulk_upsert_assets(assets_list)
        logger.info(f"Saved {count} assets")

        return count

    def get_asset_by_id(self, asset_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get a single asset by ID.

        Args:
            asset_id: Asset UUID

        Returns:
            Asset dictionary if found, None otherwise
        """
        return self.db.get_asset_by_id(asset_id)

    def get_all_assets(self, limit: Optional[int] = None) -> pd.DataFrame:
        """
        Get all assets as DataFrame.

        Args:
            limit: Optional limit on number of results

        Returns:
            DataFrame with asset data
        """
        logger.info("Loading all assets from database...")
        assets_list = self.db.get_all_assets(limit=limit)
        df = pd.DataFrame(assets_list)
        logger.info(f"Loaded {len(df)} assets")
        return df

    def get_all_historical_asset_ids(self) -> List[str]:
        """
        Get all unique asset IDs that have ever existed.

        Uses the assets table (indexed PK, small) plus asset_chronicle events
        (indexed on event_type) instead of scanning the full assets_history table.
        The UNION naturally deduplicates across both sources.

        This is used for detecting asset re-adds: if an asset existed before
        (even if it was removed), it should be marked as 'asset_readded' not 'asset_created'.

        Returns:
            List of asset ID strings
        """
        query = """
            SELECT id::text FROM assets WHERE id IS NOT NULL
            UNION
            SELECT asset_id::text FROM asset_chronicle
            WHERE event_type IN ('asset_created', 'asset_readded')
        """

        try:
            results = self.db.execute_query(query)
            historical_ids = [row['id'] for row in results] if results else []
            logger.info(f"Loaded {len(historical_ids)} historical asset IDs")
            return historical_ids
        except Exception as e:
            logger.error(f"Error loading historical asset IDs: {e}")
            return []

    def delete_asset(self, asset_id: UUID) -> bool:
        """
        Delete an asset by ID.

        Args:
            asset_id: Asset UUID

        Returns:
            True if deleted, False if not found
        """
        return self.db.delete_asset(asset_id)

    def get_asset_count(self) -> int:
        """
        Get total count of assets in database.

        Returns:
            Number of assets
        """
        stats = self.db.get_table_stats()
        return stats.get('assets', 0)

    def get_assets_by_category(self, category: str) -> pd.DataFrame:
        """
        Get all assets in a specific category.

        Args:
            category: Asset category (e.g., 'Computer', 'Network Device')

        Returns:
            DataFrame with matching assets
        """
        query = "SELECT * FROM assets WHERE category = %s"
        results = self.db.execute_query(query, (category,), fetch=True)
        return pd.DataFrame(results)

    def search_assets(
        self,
        hostname: Optional[str] = None,
        ip: Optional[str] = None,
        mac: Optional[str] = None,
        sn: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Search for assets by various criteria.

        Args:
            hostname: Hostname to search for
            ip: IP address to search for
            mac: MAC address to search for
            sn: Serial number to search for

        Returns:
            DataFrame with matching assets
        """
        conditions = []
        params = []

        if hostname:
            conditions.append("hostname ILIKE %s")
            params.append(f"%{hostname}%")

        if ip:
            conditions.append("ip = %s::inet")
            params.append(ip)

        if mac:
            conditions.append("mac = %s::macaddr")
            params.append(mac)

        if sn:
            conditions.append("sn ILIKE %s")
            params.append(f"%{sn}%")

        if not conditions:
            logger.warning("search_assets called with no search criteria")
            return pd.DataFrame()

        query = f"SELECT * FROM assets WHERE {' OR '.join(conditions)}"
        results = self.db.execute_query(query, tuple(params), fetch=True)

        return pd.DataFrame(results)

    # =========================================================================
    # LIFECYCLE TRACKING METHODS (Phase 1)
    # =========================================================================

    def get_first_seen_dates(self) -> Dict[str, Any]:
        """
        Get first_seen dates for all assets that have ever existed.

        This is used to preserve first_seen timestamps across pipeline runs.
        When an asset appears in a new generation, we check if it existed
        before and preserve its original first_seen date.

        Returns:
            Dictionary mapping asset_id (str) to first_seen timestamp
        """
        logger.debug("Loading first_seen dates from history...")

        query = """
            SELECT
                id::text as asset_id,
                MIN(first_seen) as earliest_first_seen
            FROM assets_history
            WHERE first_seen IS NOT NULL
            GROUP BY id
        """

        results = self.db.execute_query(query, (), fetch=True)

        first_seen_map = {
            r['asset_id']: r['earliest_first_seen']
            for r in results
        }

        logger.debug(f"Loaded {len(first_seen_map)} first_seen dates from history")
        return first_seen_map

    # =========================================================================
    # LIFECYCLE RECONCILIATION METHODS (Phase 2)
    # =========================================================================

    def get_asset_ids_by_run(self, generation_run_id: UUID) -> List[str]:
        """
        Get all asset IDs from a specific generation run.

        This is used for lifecycle reconciliation to determine which
        assets existed in a previous run but not in the current run.

        Args:
            generation_run_id: UUID of the generation run

        Returns:
            List of asset IDs (as strings) from that run
        """
        query = """
            SELECT id::text as asset_id
            FROM assets_history
            WHERE generation_run_id = %s
        """

        results = self.db.execute_query(query, (str(generation_run_id),), fetch=True)
        return [r['asset_id'] for r in results]

    def get_previous_generation_run_id(self, current_run_id: UUID) -> Optional[UUID]:
        """
        Get the generation_run_id that ran before current_run_id.

        Used for lifecycle reconciliation to compare current run with
        immediately previous run to detect disappeared assets.

        Args:
            current_run_id: UUID of the current generation run

        Returns:
            UUID of previous run, or None if no previous run exists
        """
        query = """
            SELECT DISTINCT generation_run_id, archived_at
            FROM assets_history
            WHERE generation_run_id != %s
            ORDER BY archived_at DESC
            LIMIT 1
        """

        results = self.db.execute_query(query, (str(current_run_id),), fetch=True)

        if results:
            return UUID(results[0]['generation_run_id'])
        else:
            logger.info("No previous generation run found")
            return None

    def get_asset_last_seen(self, asset_id: str, generation_run_id: UUID) -> Any:
        """
        Get last_seen timestamp for an asset from a specific run.

        Used to determine how long an asset has been missing when
        generating lifecycle events (inactive vs retired).

        Args:
            asset_id: Asset UUID as string
            generation_run_id: UUID of the generation run

        Returns:
            Timestamp when asset was last seen, or current time if not found
        """
        from datetime import datetime, timezone

        query = """
            SELECT last_seen
            FROM assets_history
            WHERE id = %s::uuid AND generation_run_id = %s
            LIMIT 1
        """

        results = self.db.execute_query(
            query,
            (asset_id, str(generation_run_id)),
            fetch=True
        )

        if results and results[0]['last_seen']:
            return results[0]['last_seen']
        else:
            logger.warning(
                f"Could not find last_seen for asset {asset_id} in run {generation_run_id}, "
                f"using current time"
            )
            return datetime.now(timezone.utc)
