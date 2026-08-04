# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Generation Trigger Service

Orchestrates asset generation from mapped data, connecting the data transformation
pipeline to the existing asset generation system.

This service:
- Queries raw_asset_data for batches ready for generation
- Converts PostgreSQL data to pandas DataFrame
- Calls PipelineOrchestrator.generate_assets()
- Updates ingestion_batches with asset counts and status
"""

import csv
import logging
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime

import pandas as pd
from psycopg2 import extras

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.services.pipeline_orchestrator import PipelineOrchestrator
from src.modules.integrations.repositories.batch_repository import BatchRepository
from src.modules.integrations.services.mapping_service import _compute_mapping_hash


logger = logging.getLogger(__name__)


class AssetGenerationTriggerService:
    """
    Triggers asset generation from mapped data.

    Coordinates between:
    - raw_asset_data (normalized records from Phase 6)
    - PipelineOrchestrator (staged pipeline with 21 composable stages)
    - ingestion_batches (status tracking)
    """

    def __init__(self,
                 db_adapter: Optional[PostgreSQLAdapter] = None,
                 batch_repository: Optional[BatchRepository] = None,
                 settings_manager=None,
                 posture_evaluation_service=None,
                 **kwargs):
        """
        Initialize asset generation trigger service.

        Args:
            db_adapter: PostgreSQL database adapter (creates default if not provided)
            batch_repository: Batch repository (creates default if not provided)
            settings_manager: SettingsManager instance for reading dynamic config
            posture_evaluation_service: PostureEvaluationService for pipeline stage s17b
        """
        self.db_adapter = db_adapter or PostgreSQLAdapter()
        self.batch_repository = batch_repository or BatchRepository()
        self.settings_manager = settings_manager
        self.posture_evaluation_service = posture_evaluation_service

        self.asset_gen_service = self._create_pipeline_orchestrator()

        logger.info("AssetGenerationTriggerService initialized")

    def _read_ml_kb_settings(self):
        """Read ML/KB settings from settings_manager (falls back to safe defaults).

        Called at pipeline invocation time, not init, so settings changes
        take effect without process restart.
        """
        if self.settings_manager:
            return {
                'enable_ml_prediction': self.settings_manager.get('asset_generation', 'enable_ml_predictions', False),
                'enable_knowledge_base': self.settings_manager.get('asset_generation', 'enable_knowledge_base', True),
                'rule_confidence_threshold': self.settings_manager.get('asset_generation', 'rule_confidence_threshold', 0.85),
                'ml_confidence_threshold': self.settings_manager.get('asset_generation', 'ml_confidence_threshold', 0.70),
            }
        return {
            'enable_ml_prediction': False,
            'enable_knowledge_base': True,
            'rule_confidence_threshold': 0.85,
            'ml_confidence_threshold': 0.70,
        }

    def _create_pipeline_orchestrator(self):
        """Create PipelineOrchestrator with current settings."""
        settings = self._read_ml_kb_settings()
        return PipelineOrchestrator(
            db_adapter=self.db_adapter,
            posture_evaluation_service=self.posture_evaluation_service,
            use_uuid=True,
            enable_rule_based_prediction=True,
            enable_category_overrides=True,
            enable_asset_label_overrides=True,
            enable_provider_tracking=True,
            **settings,
        )

    def _check_stale_mappings(self, batch_ids: List[UUID]) -> List[UUID]:
        """Detect batches whose mapping config has changed since they were mapped.

        For each batch that has a stored mapping_hash, computes the current hash
        from the integration's field_mapping + transformations and compares.
        Returns the list of batch IDs with a stale mapping.

        If auto-remap succeeds the batch is transparently refreshed; if it fails
        a warning is logged and the batch proceeds with potentially outdated data.
        """
        from src.modules.integrations.repositories import IntegrationRepository
        from src.modules.integrations.services.mapping_service import MappingService

        integration_repo = IntegrationRepository()
        stale_batch_ids = []
        mapping_service = None

        for batch_id in batch_ids:
            try:
                batch = self.batch_repository.get_batch(batch_id)
                if not batch or not batch.get('mapping_hash'):
                    continue

                integration_id = UUID(str(batch['integration_id']))
                integration = integration_repo.get_by_id(integration_id)
                if not integration:
                    continue

                current_hash = _compute_mapping_hash(
                    integration.field_mapping, integration.transformations
                )

                if current_hash != batch['mapping_hash']:
                    logger.warning(
                        "Stale mapping detected for batch %s "
                        "(integration %s / %s): stored_hash=%s, current_hash=%s. "
                        "Auto-remapping batch.",
                        batch_id, integration_id, integration.name,
                        batch['mapping_hash'], current_hash,
                    )
                    stale_batch_ids.append(batch_id)

                    # Attempt auto-remap so the pipeline runs on fresh data
                    if mapping_service is None:
                        mapping_service = MappingService(
                            integration_repository=integration_repo,
                            batch_repository=self.batch_repository,
                        )
                    result = mapping_service.remap_batch(batch_id)
                    if result.get('success'):
                        logger.info(
                            "Auto-remap succeeded for batch %s: "
                            "deleted=%d, mapped=%d",
                            batch_id,
                            result.get('deleted_count', 0),
                            result.get('mapped_count', 0),
                        )
                    else:
                        logger.error(
                            "Auto-remap FAILED for batch %s: %s. "
                            "Pipeline will proceed with stale data.",
                            batch_id, result.get('error', 'unknown'),
                        )

            except Exception as e:
                logger.error(
                    "Error checking stale mapping for batch %s: %s",
                    batch_id, e, exc_info=True,
                )

        return stale_batch_ids

    def generate_assets_for_batch(
        self,
        batch_id: UUID,
        on_stage_change=None,
        pipeline_run_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Generate assets from a single batch's mapped data.

        Steps:
        1. Verify batch is ready (mapped, not yet generated)
        2. Load raw_asset_data for batch
        3. Convert to pandas DataFrame
        4. Call PipelineOrchestrator.generate_assets()
        5. Update batch with asset counts
        6. Return statistics

        Args:
            batch_id: Batch UUID

        Returns:
            {
                'success': bool,
                'batch_id': str,
                'assets_created': int,
                'assets_updated': int,
                'duration_seconds': float,
                'stats': dict,
                'error': str  # Only present if success=False
            }
        """
        start_time = time.time()

        logger.info(f"Starting asset generation for batch {batch_id}")

        try:
            # 1. Verify batch exists and is ready
            batch = self.batch_repository.get_batch(batch_id)
            if not batch:
                return {
                    'success': False,
                    'batch_id': str(batch_id),
                    'error': 'Batch not found'
                }

            # Check if already generated
            if batch.get('asset_generation_status') == 'completed':
                return {
                    'success': False,
                    'batch_id': str(batch_id),
                    'error': 'Batch already generated',
                    'assets_created': batch.get('assets_created_count', 0),
                    'assets_updated': batch.get('assets_updated_count', 0)
                }

            # Check if mapping is complete
            if batch.get('status') != 'completed' or not batch.get('mapped_records_count', 0):
                return {
                    'success': False,
                    'batch_id': str(batch_id),
                    'error': 'Batch not ready for generation (mapping incomplete or no mapped records)'
                }

            # 2. Check for stale mapping before processing
            self._check_stale_mappings([batch_id])

            # 3. Update batch status to processing
            self._update_batch_generation_status(batch_id, 'processing')

            # 4. Load raw_asset_data as DataFrame
            logger.info(f"Loading raw_asset_data for batch {batch_id}")
            raw_data_df = self._load_raw_asset_data(batch_id)

            if raw_data_df.empty:
                self._update_batch_generation_status(
                    batch_id,
                    'failed',
                    error='No raw asset data found for batch'
                )
                return {
                    'success': False,
                    'batch_id': str(batch_id),
                    'error': 'No raw asset data found',
                    'duration_seconds': time.time() - start_time
                }

            logger.info(f"Loaded {len(raw_data_df)} records for generation")

            # 4. Load MAC vendor data for enrichment (optional but recommended)
            mac_vendor_df = self._load_mac_vendor_data()
            logger.info(f"Loaded {len(mac_vendor_df) if mac_vendor_df is not None else 0} MAC vendor prefixes")

            # 5. Call asset generation service (recreate to pick up fresh settings)
            self.asset_gen_service = self._create_pipeline_orchestrator()
            logger.info(f"Running asset generation pipeline on {len(raw_data_df)} records")
            generation_start = time.time()

            result_df, generation_stats = self.asset_gen_service.generate_assets(
                raw_data_df,
                mac_vendor_df=mac_vendor_df,
                track_changes=True,  # Enable chronicle tracking
                on_stage_change=on_stage_change,
                pipeline_run_id=pipeline_run_id,
            )

            generation_duration = time.time() - generation_start
            logger.info(f"Asset generation completed in {generation_duration:.2f}s")

            # 6. Extract counts from statistics (populated by chronicle stage)
            assets_created = generation_stats.get('new_assets', 0)
            assets_updated = generation_stats.get('updated_assets', 0)

            # Fallback if chronicle was skipped (track_changes=False)
            if 'new_assets' not in generation_stats:
                assets_created = len(result_df) if result_df is not None else 0

            # 7. Update batch with success status
            self._update_batch_generation_status(
                batch_id,
                'completed',
                assets_created=assets_created,
                assets_updated=assets_updated
            )

            total_duration = time.time() - start_time

            logger.info(
                f"✓ Batch {batch_id} generation complete: "
                f"{assets_created} created, {assets_updated} updated "
                f"in {total_duration:.2f}s"
            )

            return {
                'success': True,
                'batch_id': str(batch_id),
                'assets_created': assets_created,
                'assets_updated': assets_updated,
                'duration_seconds': total_duration,
                'generation_duration_seconds': generation_duration,
                'input_records': len(raw_data_df),
                'stats': generation_stats
            }

        except Exception as e:
            logger.error(f"Error generating assets for batch {batch_id}: {e}", exc_info=True)

            # Update batch with error status
            self._update_batch_generation_status(
                batch_id,
                'failed',
                error=str(e)
            )

            return {
                'success': False,
                'batch_id': str(batch_id),
                'error': str(e),
                'duration_seconds': time.time() - start_time
            }

    def generate_assets_for_latest_batches(
        self,
        on_stage_change=None,
        pipeline_run_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Generate assets from latest batch of each integration.

        This is the main entry point for scheduled generation.

        Combines raw records from ALL pending batches into a single DataFrame
        and runs the pipeline once. This is critical because:
        - UnionFind needs records from all sources to merge cross-source assets
        - The pipeline uses TRUNCATE + INSERT, so per-batch runs would overwrite
        - A single run is much faster than N sequential runs

        Returns:
            {
                'success': bool,
                'batches_processed': int,
                'batches_failed': int,
                'total_assets_created': int,
                'total_assets_updated': int,
                'duration_seconds': float,
                'batch_results': List[dict]
            }
        """
        start_time = time.time()

        logger.info("=" * 80)
        logger.info("Starting scheduled asset generation for latest batches")
        logger.info("=" * 80)

        try:
            # Find latest batches ready for generation
            pending_batches = self._get_latest_pending_batches()

            if not pending_batches:
                logger.info("No pending batches found")
                return {
                    'success': True,
                    'batches_processed': 0,
                    'batches_failed': 0,
                    'total_assets_created': 0,
                    'total_assets_updated': 0,
                    'duration_seconds': time.time() - start_time,
                    'batch_results': []
                }

            batch_ids = [UUID(b['batch_id']) for b in pending_batches]
            logger.info(f"Found {len(pending_batches)} batches ready for generation")

            # Detect and auto-remap batches with stale field_mapping configs
            stale_ids = self._check_stale_mappings(batch_ids)
            if stale_ids:
                logger.warning(
                    "Stale mapping check complete: %d/%d batches were remapped",
                    len(stale_ids), len(batch_ids),
                )

            # Mark all batches as processing
            for batch_id in batch_ids:
                self._update_batch_generation_status(batch_id, 'processing')

            # Load raw records from ALL pending batches into a single DataFrame
            if on_stage_change:
                on_stage_change("Loading Data")

            combined_df = self._load_raw_asset_data_multi(batch_ids)

            if combined_df.empty:
                for batch_id in batch_ids:
                    self._update_batch_generation_status(
                        batch_id, 'failed', error='No raw asset data found'
                    )
                return {
                    'success': False,
                    'error': 'No raw asset data found across all batches',
                    'batches_processed': 0,
                    'batches_failed': len(batch_ids),
                    'total_assets_created': 0,
                    'total_assets_updated': 0,
                    'duration_seconds': time.time() - start_time,
                    'batch_results': []
                }

            # Count records per batch for reporting
            records_per_batch = (
                combined_df.groupby('batch_id').size().to_dict()
                if 'batch_id' in combined_df.columns else {}
            )
            logger.info(
                f"Loaded {len(combined_df)} total records from "
                f"{len(pending_batches)} batches"
            )

            # Per-batch outcome for the anomaly engine's A3 routing. A batch that
            # reached generation completed successfully — there is no per-batch
            # connector-failure signal at this layer (connectors run upstream at
            # ingestion). We deliberately do NOT treat a zero-record load as a
            # failure: _get_latest_pending_batches always picks the latest batch
            # per enabled integration regardless of age, so a healthy but dormant
            # source whose raw_asset_data has aged out legitimately loads zero
            # rows — that is surfaced by A3's *missing* routing, not reframed as a
            # connector failure. This hook threads a real success/error so a
            # future ingestion-layer failure signal can flip a batch to
            # success=False without another wiring change.
            batch_outcomes = [
                {'integration_name': b.get('integration_name') or str(b['batch_id']),
                 'success': True, 'error': None}
                for b in pending_batches
            ]

            # Load MAC vendor data once
            mac_vendor_df = self._load_mac_vendor_data()

            # Run the pipeline ONCE on combined data (recreate to pick up fresh settings)
            self.asset_gen_service = self._create_pipeline_orchestrator()
            logger.info(f"Running asset generation pipeline on {len(combined_df)} combined records")
            generation_start = time.time()

            result_df, generation_stats = self.asset_gen_service.generate_assets(
                combined_df,
                mac_vendor_df=mac_vendor_df,
                track_changes=True,
                on_stage_change=on_stage_change,
                pipeline_run_id=pipeline_run_id,
                batch_outcomes=batch_outcomes,
            )

            generation_duration = time.time() - generation_start
            logger.info(f"Asset generation completed in {generation_duration:.2f}s")

            # Extract total counts (populated by chronicle stage)
            total_assets_created = generation_stats.get('new_assets', 0)
            total_assets_updated = generation_stats.get('updated_assets', 0)

            # Fallback if chronicle was skipped (track_changes=False)
            if 'new_assets' not in generation_stats:
                total_assets_created = len(result_df) if result_df is not None else 0

            # Update all batch statuses
            # Since records were merged across batches, we report per-batch
            # input counts and the shared total output.
            batch_results = []
            for batch in pending_batches:
                batch_id = UUID(batch['batch_id'])
                input_records = records_per_batch.get(str(batch_id), 0)

                self._update_batch_generation_status(
                    batch_id, 'completed',
                    assets_created=total_assets_created,
                    assets_updated=total_assets_updated
                )

                batch_results.append({
                    'success': True,
                    'batch_id': str(batch_id),
                    'integration_name': batch.get('integration_name', ''),
                    'integration_type': batch.get('integration_type', ''),
                    'input_records': input_records,
                    'assets_created': total_assets_created,
                    'assets_updated': total_assets_updated,
                    'duration_seconds': generation_duration,
                })

            duration = time.time() - start_time

            logger.info("=" * 80)
            logger.info("Scheduled asset generation complete")
            logger.info(f"Batches processed: {len(pending_batches)}/{len(pending_batches)}")
            logger.info(f"Total records: {len(combined_df)}")
            total_assets = len(result_df) if result_df is not None else 0
            logger.info(f"Total assets: {total_assets}")
            logger.info(f"Total assets created: {total_assets_created}")
            logger.info(f"Total assets updated: {total_assets_updated}")
            logger.info(f"Duration: {duration:.2f}s")
            logger.info("=" * 80)

            return {
                'success': True,
                'batches_processed': len(pending_batches),
                'batches_failed': 0,
                'total_assets': total_assets,
                'total_assets_created': total_assets_created,
                'total_assets_updated': total_assets_updated,
                'duration_seconds': duration,
                'batch_results': batch_results,
                'stats': generation_stats
            }

        except Exception as e:
            logger.error(f"Error in scheduled asset generation: {e}", exc_info=True)

            # Mark any processing batches as failed
            try:
                pending_batches_local = self._get_processing_batches()
                for batch in pending_batches_local:
                    self._update_batch_generation_status(
                        UUID(batch['batch_id']), 'failed', error=str(e)
                    )
            except Exception:
                pass

            return {
                'success': False,
                'error': str(e),
                'duration_seconds': time.time() - start_time
            }

    def _load_raw_asset_data_multi(self, batch_ids: List[UUID]) -> pd.DataFrame:
        """
        Load raw_asset_data from multiple batches into a single DataFrame.

        Args:
            batch_ids: List of batch UUIDs

        Returns:
            Combined DataFrame with raw asset data from all batches
        """
        if not batch_ids:
            return pd.DataFrame()

        # Build IN clause with parameterized placeholders
        placeholders = ', '.join(['%s'] * len(batch_ids))
        query = f"""
            SELECT
                r.id,
                r.integration_id,
                r.batch_id,
                r.hostname,
                r.fqdn,
                r.ip,
                r.mac,
                r.sn,
                r.os,
                r.manufacturer,
                r.model,
                r.vendor_category,
                r.first_time,
                r.last_time,
                r.extended_fields,
                r.ingested_at,
                r.mapped_at,
                i.name as data_source,
                i.category as data_source_category
            FROM raw_asset_data r
            JOIN integrations i ON r.integration_id = i.id
            WHERE r.batch_id IN ({placeholders})
            ORDER BY r.id
        """

        params = tuple(str(bid) for bid in batch_ids)
        records = self.db_adapter.execute_query(query, params, fetch=True)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        # Keep batch_id as string for groupby later
        df['batch_id'] = df['batch_id'].astype(str)

        logger.info(
            f"Loaded {len(df)} raw asset records from {len(batch_ids)} batches"
        )
        return df

    def _get_processing_batches(self) -> List[Dict[str, Any]]:
        """Get batches currently in 'processing' state."""
        query = """
            SELECT id as batch_id
            FROM ingestion_batches
            WHERE asset_generation_status = 'processing'
        """
        return self.db_adapter.execute_query(query, (), fetch=True)

    def _load_raw_asset_data(self, batch_id: UUID) -> pd.DataFrame:
        """
        Load raw_asset_data for batch and convert to DataFrame.

        Query includes join with integrations to get data_source and data_source_category
        which are required by AssetGenerationService.

        Args:
            batch_id: Batch UUID

        Returns:
            DataFrame with raw asset data
        """
        query = """
            SELECT
                r.id,
                r.integration_id,
                r.batch_id,
                r.hostname,
                r.fqdn,
                r.ip,
                r.mac,
                r.sn,
                r.os,
                r.manufacturer,
                r.model,
                r.vendor_category,
                r.first_time,
                r.last_time,
                r.extended_fields,
                r.ingested_at,
                r.mapped_at,
                i.name as data_source,
                i.category as data_source_category
            FROM raw_asset_data r
            JOIN integrations i ON r.integration_id = i.id
            WHERE r.batch_id = %s
            ORDER BY r.id
        """

        records = self.db_adapter.execute_query(query, (str(batch_id),), fetch=True)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)

        logger.debug(f"Loaded {len(df)} raw asset records for batch {batch_id}")
        logger.debug(f"Columns: {df.columns.tolist()}")

        return df

    def _load_mac_vendor_data(self) -> Optional[pd.DataFrame]:
        """
        Load MAC vendor ranges for enrichment.

        If the mac_vendor_ranges table is empty, auto-seeds it from the
        bundled data/mac_ranges.csv (included in the Docker base image).

        Returns:
            DataFrame with MAC vendor data, or None if not available
        """
        try:
            query = "SELECT mac_prefix, vendor FROM mac_vendor_ranges"
            records = self.db_adapter.execute_query(query, (), fetch=True)

            if not records:
                logger.warning("No MAC vendor data in database — attempting auto-seed from CSV")
                self._seed_mac_vendors_from_csv()
                records = self.db_adapter.execute_query(query, (), fetch=True)

            if not records:
                logger.warning("No MAC vendor data available (seed also failed)")
                return None

            df = pd.DataFrame(records)
            logger.info(f"Loaded {len(df)} MAC vendor prefixes")
            return df

        except Exception as e:
            logger.warning(f"Failed to load MAC vendor data: {e}")
            return None

    def _seed_mac_vendors_from_csv(self) -> None:
        """
        Seed mac_vendor_ranges from the bundled data/mac_ranges.csv.

        CSV format: vendor,mac_range (e.g. "dell inc.,00:00:00:00:00:00 - 00:00:00:ff:ff:ff")
        Extracts the OUI prefix (first 3 octets) and bulk-inserts into the DB.
        """
        # Resolve CSV path (__file__-relative works in both Docker and local dev)
        # parents[4] = project root from src/modules/integrations/services/
        csv_path = Path(__file__).resolve().parents[4] / 'data' / 'mac_ranges.csv'
        if not csv_path.exists():
            logger.warning("mac_ranges.csv not found at %s — cannot auto-seed", csv_path)
            return

        try:
            rows = []
            with open(csv_path, newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    vendor = row.get('vendor', '').strip()
                    mac_range = row.get('mac_range', '').strip()
                    if not vendor or not mac_range:
                        continue
                    # Extract OUI prefix: first 8 chars of range start → "00:00:00"
                    prefix = mac_range[:8].upper()
                    if len(prefix) == 8 and prefix[2] == ':' and prefix[5] == ':':
                        rows.append((prefix, vendor))

            if not rows:
                logger.warning("No valid entries found in %s", csv_path)
                return

            # Bulk insert — ON CONFLICT handles CSV duplicates (MA-M/MA-S blocks)
            query = """
                INSERT INTO mac_vendor_ranges (mac_prefix, vendor)
                VALUES %s
                ON CONFLICT (mac_prefix) DO NOTHING
            """
            with self.db_adapter.conn_manager.get_cursor() as (cursor, conn):
                extras.execute_values(
                    cursor, query, rows,
                    template="(%s, %s)",
                    page_size=1000,
                )
                count = cursor.rowcount
                self.db_adapter.conn_manager.commit_unless_scoped(conn)

            logger.info(
                "Auto-seeded %d MAC vendor prefixes from %s (%d rows in CSV)",
                count, csv_path.name, len(rows),
            )

        except Exception as e:
            logger.warning("Failed to seed MAC vendor data from CSV: %s", e)

    def _get_latest_pending_batches(self) -> List[Dict[str, Any]]:
        """
        Get latest completed batch from each integration for asset generation.

        Always picks the most recent completed batch per integration,
        regardless of whether it was already processed. The pipeline
        uses TRUNCATE + INSERT so it needs all sources every run.

        Returns:
            List of batch dictionaries with batch_id and integration_id
        """
        query = """
            SELECT DISTINCT ON (b.integration_id)
                b.id as batch_id,
                b.integration_id,
                b.started_at,
                b.completed_at,
                b.mapped_records_count,
                i.name as integration_name,
                i.integration_type
            FROM ingestion_batches b
            JOIN integrations i ON b.integration_id = i.id
            WHERE b.status = 'completed'
            AND b.mapped_records_count > 0
            AND i.status = 'enabled'
            ORDER BY b.integration_id, b.started_at DESC
        """

        batches = self.db_adapter.execute_query(query, (), fetch=True)

        logger.debug(f"Found {len(batches)} pending batches")
        return batches

    def _update_batch_generation_status(
        self,
        batch_id: UUID,
        status: str,
        assets_created: int = 0,
        assets_updated: int = 0,
        error: Optional[str] = None
    ) -> None:
        """
        Update batch with asset generation status.

        Args:
            batch_id: Batch UUID
            status: Status value ('processing', 'completed', 'failed')
            assets_created: Number of assets created
            assets_updated: Number of assets updated
            error: Error message if failed
        """
        now = datetime.utcnow()

        if status == 'processing':
            # Mark as started
            query = """
                UPDATE ingestion_batches
                SET asset_generation_status = %s,
                    asset_generation_started_at = %s
                WHERE id = %s
            """
            params = (status, now, str(batch_id))

        elif status == 'completed':
            # Mark as completed with counts
            query = """
                UPDATE ingestion_batches
                SET asset_generation_status = %s,
                    asset_generation_completed_at = %s,
                    assets_created_count = %s,
                    assets_updated_count = %s
                WHERE id = %s
            """
            params = (status, now, assets_created, assets_updated, str(batch_id))

        elif status == 'failed':
            # Mark as failed with error
            query = """
                UPDATE ingestion_batches
                SET asset_generation_status = %s,
                    asset_generation_completed_at = %s,
                    error_message = %s
                WHERE id = %s
            """
            error_msg = error or 'Unknown error'
            params = (status, now, error_msg, str(batch_id))

        else:
            logger.error(f"Invalid status: {status}")
            return

        self.db_adapter.execute_query(query, params, fetch=False)

        logger.debug(f"Updated batch {batch_id} with status: {status}")

    def get_generation_status_overview(self) -> Dict[str, Any]:
        """
        Get overview of asset generation status across all batches.

        Returns:
            {
                'pending_batches': int,
                'processing_batches': int,
                'completed_batches': int,
                'failed_batches': int,
                'last_generation': {
                    'batch_id': str,
                    'completed_at': str,
                    'assets_created': int,
                    'assets_updated': int
                },
                'total_assets_generated': int
            }
        """
        try:
            # Count by status
            status_query = """
                SELECT
                    COUNT(*) FILTER (WHERE asset_generation_status IS NULL AND mapped_records_count > 0) as pending,
                    COUNT(*) FILTER (WHERE asset_generation_status = 'processing') as processing,
                    COUNT(*) FILTER (WHERE asset_generation_status = 'completed') as completed,
                    COUNT(*) FILTER (WHERE asset_generation_status = 'failed') as failed
                FROM ingestion_batches
                WHERE status = 'completed'
            """
            status_counts = self.db_adapter.execute_query(status_query, (), fetch=True)[0]

            # Get last completed generation
            last_gen_query = """
                SELECT
                    id as batch_id,
                    asset_generation_completed_at as completed_at,
                    assets_created_count as assets_created,
                    assets_updated_count as assets_updated
                FROM ingestion_batches
                WHERE asset_generation_status = 'completed'
                ORDER BY asset_generation_completed_at DESC
                LIMIT 1
            """
            last_gen = self.db_adapter.execute_query(last_gen_query, (), fetch=True)

            # Get total assets
            total_query = "SELECT COUNT(*) as total FROM assets"
            total_assets = self.db_adapter.execute_query(total_query, (), fetch=True)[0]['total']

            return {
                'pending_batches': status_counts['pending'] or 0,
                'processing_batches': status_counts['processing'] or 0,
                'completed_batches': status_counts['completed'] or 0,
                'failed_batches': status_counts['failed'] or 0,
                'last_generation': last_gen[0] if last_gen else None,
                'total_assets_generated': total_assets
            }

        except Exception as e:
            logger.error(f"Error getting generation status overview: {e}", exc_info=True)
            return {
                'error': str(e)
            }

    def get_integration_generation_history(
        self,
        integration_id: UUID,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get asset generation history for an integration.

        Args:
            integration_id: Integration UUID
            limit: Maximum number of records
            offset: Pagination offset

        Returns:
            List of batches with generation statistics
        """
        query = """
            SELECT
                id as batch_id,
                started_at,
                completed_at,
                asset_generation_started_at,
                asset_generation_completed_at,
                asset_generation_status,
                mapped_records_count,
                assets_created_count,
                assets_updated_count,
                error_message
            FROM ingestion_batches
            WHERE integration_id = %s
            AND asset_generation_status IS NOT NULL
            ORDER BY asset_generation_completed_at DESC NULLS LAST, started_at DESC
            LIMIT %s OFFSET %s
        """

        batches = self.db_adapter.execute_query(
            query,
            (str(integration_id), limit, offset),
            fetch=True
        )

        return batches
