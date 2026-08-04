# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Provider Repository - Persistence layer for asset provider data.

This module is responsible for:
- Saving provider data (per-source asset records)
- Querying providers by asset ID
- Bulk provider operations
"""

import logging
from typing import List, Dict, Any, Optional
from uuid import UUID

import pandas as pd

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class ProviderRepository:
    """
    Repository for asset provider data persistence operations.

    Provider data represents the raw per-source records before aggregation,
    allowing us to track which data sources contributed to each asset.
    """

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize provider repository.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter
        logger.info("ProviderRepository initialized")

    def save_providers(self, providers_df: pd.DataFrame) -> int:
        """
        Save provider data from DataFrame to database.

        Args:
            providers_df: DataFrame with provider data

        Returns:
            Number of provider records saved
        """
        logger.info(f"Saving {len(providers_df)} provider records to database...")

        # Convert DataFrame to list of dicts
        providers_list = providers_df.to_dict('records')

        # Use bulk insert (providers don't need upsert, they're immutable snapshots)
        count = self._bulk_insert_providers(providers_list)
        logger.info(f"Saved {count} provider records")

        return count

    def _bulk_insert_providers(self, providers: List[Dict[str, Any]]) -> int:
        """
        Bulk insert provider records.

        Args:
            providers: List of provider dictionaries

        Returns:
            Number of records inserted
        """
        if not providers:
            return 0

        # Build column list from first record
        columns = list(providers[0].keys())

        # Build values list
        values_list = []
        for provider in providers:
            row = []
            for col in columns:
                val = provider.get(col)

                # Convert UUID to string
                if isinstance(val, UUID):
                    row.append(str(val))
                # Convert dict to JSON (clean NaN first)
                elif isinstance(val, dict):
                    import psycopg2.extras
                    from src.database.postgres_adapter import _clean_nan
                    row.append(psycopg2.extras.Json(_clean_nan(val)))
                # Convert pandas NaN to None for DB compatibility
                elif isinstance(val, float) and val != val:
                    row.append(None)
                else:
                    row.append(val)

            values_list.append(tuple(row))

        # Build INSERT query - execute_values will add the VALUES clause
        columns_str = ', '.join(columns)

        query = f"INSERT INTO asset_providers ({columns_str}) VALUES %s"

        # Execute bulk insert
        try:
            from psycopg2.extras import execute_values

            with self.db.conn_manager.get_connection() as conn:
                with conn.cursor() as cursor:
                    execute_values(cursor, query, values_list)
                    if not self.db.conn_manager.is_scope_active():
                        conn.commit()
                    return len(values_list)

        except Exception as e:
            logger.error(f"Error bulk inserting providers: {e}")
            raise

    def get_providers_by_asset_id(
        self,
        asset_id: UUID,
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Get all provider records for a specific asset ID.

        Args:
            asset_id: Asset UUID
            limit: Optional limit on number of results

        Returns:
            DataFrame with provider records
        """
        query = "SELECT * FROM asset_providers WHERE id = %s::uuid"

        if limit:
            query += f" LIMIT {limit}"

        try:
            results = self.db.execute_query(query, (str(asset_id),), fetch=True)
            return pd.DataFrame(results)
        except Exception as e:
            logger.error(f"Error getting providers for asset {asset_id}: {e}")
            return pd.DataFrame()

    def get_all_providers(self, limit: Optional[int] = None) -> pd.DataFrame:
        """
        Get all provider records.

        Args:
            limit: Optional limit on number of results

        Returns:
            DataFrame with provider records
        """
        logger.info("Loading all provider records from database...")

        query = "SELECT * FROM asset_providers"

        if limit:
            query += f" LIMIT {limit}"

        try:
            results = self.db.execute_query(query, fetch=True)
            df = pd.DataFrame(results)
            logger.info(f"Loaded {len(df)} provider records")
            return df
        except Exception as e:
            logger.error(f"Error getting all providers: {e}")
            return pd.DataFrame()

    def delete_providers_by_asset_id(self, asset_id: UUID) -> bool:
        """
        Delete all provider records for an asset.

        Args:
            asset_id: Asset UUID

        Returns:
            True if deleted, False otherwise
        """
        query = "DELETE FROM asset_providers WHERE id = %s::uuid"

        try:
            self.db.execute_query(query, (str(asset_id),))
            logger.info(f"Deleted provider records for asset {asset_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting providers for asset {asset_id}: {e}")
            return False

    def get_provider_count(self) -> int:
        """
        Get total count of provider records.

        Returns:
            Number of provider records
        """
        query = "SELECT COUNT(*) as count FROM asset_providers"

        try:
            result = self.db.execute_query(query, fetch=True)
            return result[0]['count'] if result else 0
        except Exception as e:
            logger.error(f"Error getting provider count: {e}")
            return 0

    def preprocess_for_providers(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Preprocess asset data for provider storage.

        Removes temporary/computed columns and prepares data for provider table.
        Converts category prediction columns to JSONB format.

        Args:
            df: DataFrame with asset data (must have 'id' column with asset UUID)

        Returns:
            Preprocessed DataFrame ready for provider storage
        """
        logger.info(f"Preprocessing {len(df)} records for provider storage...")

        # Create a copy to avoid modifying original
        processed_df = df.copy()

        # Rename 'id' to 'asset_id' for providers table
        if 'id' in processed_df.columns:
            processed_df = processed_df.rename(columns={'id': 'asset_id'})
            logger.info("Renamed 'id' column to 'asset_id'")
        elif 'asset_id' not in processed_df.columns:
            logger.error("Neither 'id' nor 'asset_id' column found in DataFrame!")
            raise ValueError("DataFrame must have 'id' or 'asset_id' column")

        # Convert category prediction columns to JSONB format
        if all(col in processed_df.columns for col in ['predicted_category', 'predicted_subcategory', 'category_confidence', 'category_rule_type']):
            def create_prediction_json(row):
                """Create JSONB structure for category prediction."""
                if pd.isna(row.get('predicted_category')):
                    return None

                return {
                    'category': row.get('predicted_category'),
                    'subcategory': row.get('predicted_subcategory'),
                    'confidence': float(row.get('category_confidence')) if pd.notna(row.get('category_confidence')) else None,
                    'rule_type': row.get('category_rule_type')
                }

            processed_df['category_prediction'] = processed_df.apply(create_prediction_json, axis=1)
            logger.info(f"Created category_prediction JSONB column for {processed_df['category_prediction'].notna().sum()} records")

            # Extract category and subcategory into separate columns for V2 consistency checks
            # These represent the predicted values at provider level (before aggregation)
            processed_df['category'] = processed_df['predicted_category']
            processed_df['subcategory'] = processed_df['predicted_subcategory']
            logger.info(f"Extracted category/subcategory from predictions for V2 consistency checks")

            # Drop the individual prediction columns
            processed_df = processed_df.drop(columns=['predicted_category', 'predicted_subcategory', 'category_confidence', 'category_rule_type'], errors='ignore')

        # Drop _best columns (these are post-aggregation)
        columns_to_drop = [col for col in processed_df.columns if col.endswith('_best')]
        if columns_to_drop:
            processed_df = processed_df.drop(columns=columns_to_drop, errors='ignore')
            logger.info(f"Dropped {len(columns_to_drop)} _best columns")

        # Preserve original OS value (not enriched/normalized)
        # If os_orig exists, use it as the original OS value
        if 'os_orig' in processed_df.columns:
            processed_df['os'] = processed_df['os_orig']
            logger.info("Using 'os_orig' as original OS value")
        elif 'os' not in processed_df.columns:
            # If no OS column exists at all, set to None
            processed_df['os'] = None
            logger.info("No original OS column found, setting to None")

        # Drop OS enrichment/processing columns - we keep os_normalized for V2 consistency checks
        # Note: os_normalized is kept as it's needed for provider-level consistency detection
        os_enrichment_columns = [
            'os_short', 'os_confidence', 'os_enrichment',
            'os_orig', 'os_best_source', 'os_x', 'os_y'
        ]
        existing_os_cols = [col for col in os_enrichment_columns if col in processed_df.columns]
        if existing_os_cols:
            processed_df = processed_df.drop(columns=existing_os_cols, errors='ignore')
            logger.info(f"Dropped {len(existing_os_cols)} OS enrichment columns (keeping os_normalized for consistency checks)")

        # Drop other temporary columns
        temp_columns = ['mac_vendor_enriched', 'asset', 'asset_field']
        existing_temp_cols = [col for col in temp_columns if col in processed_df.columns]
        if existing_temp_cols:
            processed_df = processed_df.drop(columns=existing_temp_cols, errors='ignore')
            logger.info(f"Dropped {len(existing_temp_cols)} temporary columns")

        # Define fixed columns that go into dedicated database columns
        fixed_columns = [
            'asset_id', 'data_source', 'data_source_category',
            'integration_id', 'generation_run_id',
            'hostname', 'fqdn', 'ip', 'mac', 'sn', 'os',
            'manufacturer', 'model', 'vendor_category',
            'os_normalized', 'category', 'subcategory',  # Added for V2 consistency checks
            'category_prediction'
        ]

        # Collect all remaining columns into data_source_fields JSONB
        # This preserves ALL original data + enrichments
        remaining_columns = [col for col in processed_df.columns if col not in fixed_columns]

        if remaining_columns:
            logger.info(f"Collecting {len(remaining_columns)} additional columns into data_source_fields: {remaining_columns}")

            def create_data_source_fields(row):
                """Collect all non-fixed columns into a dict."""
                fields_dict = {}
                for col in remaining_columns:
                    value = row.get(col)

                    # Skip None values
                    if value is None:
                        continue

                    # Handle different value types
                    try:
                        # For scalar values, check if NaN
                        if isinstance(value, (int, float, str, bool)):
                            if pd.isna(value):
                                continue
                            fields_dict[col] = value
                        # For lists, arrays, dicts - include as-is (JSON serializable)
                        elif isinstance(value, (list, dict)):
                            fields_dict[col] = value
                        # For other types, convert to string
                        else:
                            fields_dict[col] = str(value)
                    except Exception as e:
                        # If we can't determine, try to include it
                        logger.warning(f"Could not process value for column '{col}': {e}")
                        continue

                return fields_dict if fields_dict else None

            processed_df['data_source_fields'] = processed_df.apply(create_data_source_fields, axis=1)
            logger.info(f"Created data_source_fields for {processed_df['data_source_fields'].notna().sum()} records")
        else:
            processed_df['data_source_fields'] = None
            logger.info("No additional columns to preserve in data_source_fields")

        # Keep only the fixed columns + data_source_fields
        provider_columns = fixed_columns + ['data_source_fields']

        # Filter to only existing columns
        available_columns = [col for col in provider_columns if col in processed_df.columns]
        processed_df = processed_df[available_columns]

        # Deduplicate provider records by identity key (#283)
        # Same device from same source with same NIC should appear once.
        # Keep the last record (most recently ingested) to retain freshest metadata.
        dedup_key = ['asset_id', 'data_source', 'hostname', 'mac', 'sn', 'ip']
        required_dedup_cols = ('asset_id', 'data_source')
        available_dedup_cols = [c for c in dedup_key if c in processed_df.columns]
        if all(c in processed_df.columns for c in required_dedup_cols):
            before = len(processed_df)
            shadow = processed_df[available_dedup_cols].apply(
                lambda col: col.map(lambda x: str(x) if isinstance(x, list) else x)
            )
            processed_df = processed_df[~shadow.duplicated(keep='last')]
            deduped = before - len(processed_df)
            if deduped:
                logger.info(
                    f"Provider dedup removed {deduped} duplicate records "
                    f"(kept {len(processed_df)} of {before})"
                )

        logger.info(f"Provider preprocessing complete: {len(processed_df)} records with {len(available_columns)} columns")
        logger.info(f"Fixed columns: {[c for c in fixed_columns if c in available_columns]}")

        return processed_df
