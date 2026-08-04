# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Category Override Repository - Persistence layer for manual category overrides.

This module is responsible for:
- Loading category overrides from database
- Applying overrides to asset data
- Tracking original ML-predicted categories
"""

import logging
from typing import Any, Dict, List, Set
from uuid import UUID

import pandas as pd
from psycopg2 import extras
from psycopg2.extras import Json

from src.database.postgres_adapter import _clean_nan

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class CategoryOverrideRepository:
    """
    Repository for category override operations.

    Provides interface for loading and applying manual category overrides
    that supersede ML predictions.
    """

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize category override repository.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter
        logger.info("CategoryOverrideRepository initialized")

    def get_all_overrides(self) -> Dict[str, Dict[str, str]]:
        """
        Get all category overrides from database.

        Returns:
            Dictionary mapping asset_id (str) -> {"category": str, "subcategory": str|None}
        """
        logger.info("Loading category overrides from database...")

        query = """
            SELECT asset_id, category, subcategory
            FROM category_overrides
            WHERE active = true
        """

        try:
            results = self.db.execute_query(query, fetch=True)

            overrides = {
                str(row['asset_id']): {
                    "category": row['category'].lower(),
                    "subcategory": row.get('subcategory'),
                }
                for row in results
            }

            logger.info(f"Loaded {len(overrides)} category overrides")
            return overrides

        except Exception as e:
            logger.error(f"Error loading category overrides: {e}")
            return {}

    def get_active_override_ids(self, asset_ids: List[str]) -> Set[str]:
        """Which of `asset_ids` currently carry an active override.

        The durable answer to "is this asset overridden?". `assets.
        category_is_overridden` cannot answer it — every pipeline run with work
        resets that column to its DB DEFAULT — whereas these rows have no FK to
        `assets` and survive the rebuild untouched (#1270).

        Errors propagate — deliberately unlike `get_all_overrides` /
        `get_override_count`, which degrade to empty. Those serve read paths; this
        one is called inside `clear_overrides_bulk`'s `_bulk_scope()` write
        transaction, where an empty result is indistinguishable from "nothing was
        overridden". Swallowing here would leave the transaction INERROR without
        setting the scope's `errored` flag, so `end_scope()` would roll back with
        only a warning (it passes no `raise_on_aborted`) and the caller would get
        HTTP 200 and `{"cleared": 0}` for an operation that failed outright —
        #813's shape, and the silent success #1270 is about.

        Args:
            asset_ids: Asset ids to check (str or UUID)

        Returns:
            The subset that has `active = true`, as strings so the result
            composes with the caller's `List[str]`.
        """
        if not asset_ids:
            return set()

        query = """
            SELECT asset_id
            FROM category_overrides
            WHERE active = true
              AND asset_id = ANY(%(ids)s::uuid[])
        """

        results = self.db.execute_query(
            query, {'ids': [str(a) for a in asset_ids]}, fetch=True
        )
        return {str(row['asset_id']) for row in (results or [])}

    def get_active_override(self, asset_id) -> Dict[str, Any] | None:
        """The active override row for one asset, with its attribution, or None.

        The per-asset twin of `get_active_override_ids`, and the durable answer
        to "was this asset reclassified by hand, and by whom?". `assets.
        category_is_overridden` cannot answer it — every pipeline run with work
        resets that column to its DB DEFAULT — whereas these rows have no FK to
        `assets` and survive the rebuild (#1270).

        `active = true` rather than "a row exists": clearing deactivates the row
        instead of deleting it, so an asset that was overridden and then restored
        would otherwise read as overridden forever.

        Errors propagate — deliberately unlike `get_all_overrides` /
        `get_override_count`, which degrade to empty. Those feed the pipeline,
        where a missing override is a tolerable degradation. This one backs the
        edit modal, which renders an empty answer as "never reclassified" and
        hides both the indicator and the restore control: swallowing here turns
        a failed read into a confident falsehood served with an HTTP 200 (#1155).

        Args:
            asset_id: Asset id (str or UUID)

        Returns:
            The row (asset_id, category, subcategory, reason, overridden_by,
            overridden_at, updated_at), or None when nothing is active.
        """
        query = """
            SELECT asset_id, category, subcategory, reason,
                   overridden_by, overridden_at, updated_at
            FROM category_overrides
            WHERE asset_id = %(asset_id)s::uuid
              AND active = true
        """

        results = self.db.execute_query(query, {'asset_id': str(asset_id)}, fetch=True)
        return results[0] if results else None

    @staticmethod
    def snapshot_predictions(
        df: pd.DataFrame,
        category_column: str = 'category_best'
    ) -> pd.DataFrame:
        """Freeze the pipeline's own category/subcategory into the *_predicted
        columns that get persisted to the assets table.

        MUST run before any override is applied: these columns are what clearing
        an override restores, so snapshotting a post-override value would freeze
        the override in as the "prediction" and make the restore a no-op that
        looks like a bug (#1154). Stage 13 calls this on every run — including
        runs with overrides disabled, which is most of them — and apply_overrides
        calls it again on its own pre-override frame, so the two agree by
        construction rather than by two copies of the same three lines.

        Column names differ by pipeline position: s14 renames `category_best` to
        `category`, so accept either shape and resolve each field independently.

        Args:
            df: DataFrame with asset data
            category_column: Preferred category column, falling back to 'category'

        Returns:
            The same DataFrame, with the *_predicted columns set where a source
            column exists. A frame carrying neither is left untouched.
        """
        cat_col = category_column if category_column in df.columns else 'category'
        sub_col = 'subcategory_best' if 'subcategory_best' in df.columns else 'subcategory'
        if cat_col in df.columns:
            df['category_predicted'] = df[cat_col].copy()
        if sub_col in df.columns:
            df['subcategory_predicted'] = df[sub_col].copy()
        return df

    def apply_overrides(
        self,
        df: pd.DataFrame,
        overrides: Dict[str, Dict[str, str]],
        category_column: str = 'category_best'
    ) -> pd.DataFrame:
        """
        Apply category overrides to assets and preserve original predictions.

        Args:
            df: DataFrame with asset data
            overrides: Dictionary of asset_id -> {"category": str, "subcategory": str|None}
            category_column: Column name to override (default: 'category_best')

        Returns:
            DataFrame with overrides applied and the *_predicted columns set
        """
        if not overrides:
            logger.info("No category overrides to apply")
            # Still snapshot the predictions for consistency
            self.snapshot_predictions(df, category_column)
            df['category_is_overridden'] = False
            return df

        logger.info(f"Applying {len(overrides)} category overrides...")

        # Check if we have category_best column (created by select_best_category)
        if category_column not in df.columns:
            # Fall back to 'category' if _best doesn't exist
            if 'category' in df.columns:
                category_column = 'category'
            else:
                logger.warning(f"Column {category_column} not found, cannot apply overrides")
                return df

        # Preserve the predictions before anything below overwrites them
        self.snapshot_predictions(df, category_column)

        # Build category-only map for applying to the category column
        category_map = {aid: o["category"] for aid, o in overrides.items()}
        subcategory_map = {aid: o["subcategory"] for aid, o in overrides.items() if o.get("subcategory")}

        # Apply overrides
        # Convert id column to string for matching
        df['id_str'] = df['id'].astype(str)
        mask = df['id_str'].isin(overrides.keys())

        # Log the overrides being applied
        overrides_applied = df[mask]['id_str'].tolist()
        if overrides_applied:
            logger.info(f"Applying overrides to {len(overrides_applied)} assets")

            # Log before and after for debugging (first 10)
            for idx in df[mask].head(10).index:
                id_val = df.loc[idx, 'id_str']
                orig_cat = df.loc[idx, category_column]
                new_cat = category_map[id_val]
                logger.debug(f"Asset ID {id_val}: '{orig_cat}' -> '{new_cat}'")

        # Apply category overrides
        df.loc[mask, category_column] = df.loc[mask, 'id_str'].map(category_map)

        # Apply subcategory overrides (check both column names — _best exists
        # before the pipeline renames it to subcategory)
        sub_col = 'subcategory_best' if 'subcategory_best' in df.columns else 'subcategory'
        if subcategory_map and sub_col in df.columns:
            sub_mask = df['id_str'].isin(subcategory_map.keys())
            df.loc[sub_mask, sub_col] = df.loc[sub_mask, 'id_str'].map(subcategory_map)

        # Mark overridden assets for database persistence (#48)
        df['category_is_overridden'] = mask

        # Clean up temporary column
        df.drop('id_str', axis=1, inplace=True)

        logger.info(f"Applied {mask.sum()} category overrides")
        return df

    def save_override(
        self,
        asset_id: UUID,
        category: str,
        reason: str = None,
        subcategory: str = None,
        overridden_by: str = None,
        features_snapshot: dict = None,
    ) -> bool:
        """
        Save a single category override.

        Args:
            asset_id: Asset UUID
            category: Override category
            reason: Optional reason for override
            subcategory: Optional override subcategory
            overridden_by: Optional username of who made the override
            features_snapshot: Optional dict of ML features at override time

        Returns:
            True if successful, False otherwise
        """
        query = """
            INSERT INTO category_overrides (asset_id, category, subcategory, reason, overridden_by, active, features_snapshot)
            VALUES (%s::uuid, %s, %s, %s, %s, true, %s)
            ON CONFLICT (asset_id)
            DO UPDATE SET
                category = EXCLUDED.category,
                subcategory = EXCLUDED.subcategory,
                reason = EXCLUDED.reason,
                overridden_by = EXCLUDED.overridden_by,
                -- Restamped with the rest of the row: asset_id is the PK, so
                -- this UPDATE *replaces* the override rather than amending it.
                -- Leaving overridden_at at the first override's value while
                -- overridden_by moves to the new author attributes one person's
                -- correction to another one's timestamp — visible verbatim in
                -- the edit modal's "<date> by <user>" line (#1155).
                overridden_at = CURRENT_TIMESTAMP,
                active = EXCLUDED.active,
                features_snapshot = EXCLUDED.features_snapshot,
                updated_at = CURRENT_TIMESTAMP
        """

        try:
            self.db.execute_query(
                query,
                (str(asset_id), category.lower(), subcategory, reason, overridden_by,
                 Json(_clean_nan(features_snapshot)) if features_snapshot else None),
                fetch=False,
            )
            logger.info(f"Saved category override for asset {asset_id}: {category}/{subcategory}")
            return True

        except Exception as e:
            logger.error(f"Error saving category override: {e}")
            return False

    def delete_override(self, asset_id: UUID) -> bool:
        """
        Delete (deactivate) a category override.

        Args:
            asset_id: Asset UUID

        Returns:
            True if successful, False otherwise
        """
        query = """
            UPDATE category_overrides
            SET active = false, updated_at = CURRENT_TIMESTAMP
            WHERE asset_id = %s::uuid
        """

        try:
            self.db.execute_query(query, (str(asset_id),), fetch=False)
            logger.info(f"Deactivated category override for asset {asset_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting category override: {e}")
            return False

    def save_overrides_bulk(
        self,
        asset_ids: List[str],
        category: str,
        subcategory: str = None,
        reason: str = None,
        overridden_by: str = None,
    ) -> int:
        """UPSERT one active override per asset id (same shape as save_override,
        batched). features_snapshot is NULL for bulk — only single-asset
        explainability needs it. Stores category lowercased to match
        save_override / get_all_overrides.
        """
        if not asset_ids:
            return 0

        query = """
            INSERT INTO category_overrides
                (asset_id, category, subcategory, reason, overridden_by, active, features_snapshot)
            VALUES %s
            ON CONFLICT (asset_id) DO UPDATE SET
                category = EXCLUDED.category,
                subcategory = EXCLUDED.subcategory,
                reason = EXCLUDED.reason,
                overridden_by = EXCLUDED.overridden_by,
                -- Restamped alongside overridden_by, same reason as save_override.
                overridden_at = CURRENT_TIMESTAMP,
                active = EXCLUDED.active,
                features_snapshot = EXCLUDED.features_snapshot,
                updated_at = CURRENT_TIMESTAMP
        """
        cat = category.lower()
        values = [
            (str(aid), cat, subcategory, reason, overridden_by, True, None)
            for aid in asset_ids
        ]
        with self.db.conn_manager.get_cursor() as (cursor, conn):
            extras.execute_values(
                cursor, query, values,
                template="(%s::uuid, %s, %s, %s, %s, %s, %s)", page_size=1000,
            )
            count = cursor.rowcount
            self.db.conn_manager.commit_unless_scoped(conn)
        logger.info(f"Bulk saved {len(asset_ids)} category overrides: {category}/{subcategory}")
        return count

    def deactivate_overrides_bulk(self, asset_ids: List[str]) -> int:
        """Deactivate overrides for many assets in one statement."""
        if not asset_ids:
            return 0
        query = """
            UPDATE category_overrides
            SET active = false, updated_at = CURRENT_TIMESTAMP
            WHERE asset_id = ANY(%(ids)s::uuid[])
        """
        self.db.execute_query(query, {'ids': [str(a) for a in asset_ids]}, fetch=False)
        logger.info(f"Bulk deactivated category overrides for {len(asset_ids)} assets")
        return len(asset_ids)

    def get_override_count(self) -> int:
        """
        Get count of active category overrides.

        Returns:
            Number of active overrides
        """
        query = "SELECT COUNT(*) as count FROM category_overrides WHERE active = true"

        try:
            result = self.db.execute_query(query, fetch=True)
            return result[0]['count'] if result else 0
        except Exception as e:
            logger.error(f"Error getting override count: {e}")
            return 0
