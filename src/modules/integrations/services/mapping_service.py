# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Mapping Service

Orchestrates transformation of raw source data to normalized asset data.
"""

import hashlib
import json
import logging
from typing import Dict, Any, List, Optional
from uuid import UUID

from src.modules.integrations.repositories import IntegrationRepository
from src.modules.integrations.repositories.batch_repository import BatchRepository
from src.modules.integrations.repositories.raw_data_repository import RawDataRepository
from src.modules.integrations.repositories.raw_asset_repository import RawAssetRepository
from src.modules.integrations.services.field_mapper import FieldMapper


logger = logging.getLogger(__name__)


def _compute_mapping_hash(field_mapping: dict, transformations: dict = None) -> str:
    """Compute deterministic hash of mapping config.

    Returns the first 16 hex chars of a SHA-256 digest so the hash is
    compact enough for a VARCHAR(16) column but still collision-resistant
    for practical purposes.
    """
    config = {
        'field_mapping': field_mapping or {},
        'transformations': transformations or {}
    }
    return hashlib.sha256(
        json.dumps(config, sort_keys=True).encode()
    ).hexdigest()[:16]


class MappingService:
    """
    Service for transforming raw data to normalized asset data.

    Orchestrates the complete mapping process:
    1. Read raw_source_data for batch
    2. Apply field mapping
    3. Store in raw_asset_data
    4. Update batch status
    """

    def __init__(self,
                 integration_repository: Optional[IntegrationRepository] = None,
                 batch_repository: Optional[BatchRepository] = None,
                 raw_data_repository: Optional[RawDataRepository] = None,
                 raw_asset_repository: Optional[RawAssetRepository] = None):
        """
        Initialize mapping service.

        Args:
            integration_repository: Integration repository (creates default if not provided)
            batch_repository: Batch repository (creates default if not provided)
            raw_data_repository: Raw data repository (creates default if not provided)
            raw_asset_repository: Raw asset repository (creates default if not provided)
        """
        self.integration_repository = integration_repository or IntegrationRepository()
        self.batch_repository = batch_repository or BatchRepository()
        self.raw_data_repository = raw_data_repository or RawDataRepository()
        self.raw_asset_repository = raw_asset_repository or RawAssetRepository()

    def map_batch(self, batch_id: UUID) -> Dict[str, Any]:
        """
        Map all raw records in a batch to raw_asset_data.

        Args:
            batch_id: Batch UUID

        Returns:
            {
                'success': bool,
                'batch_id': str,
                'mapped_count': int,
                'failed_count': int,
                'errors': List[str]
            }
        """
        # Get batch
        batch = self.batch_repository.get_batch(batch_id)
        if not batch:
            return {
                'success': False,
                'error': 'Batch not found',
                'batch_id': str(batch_id)
            }

        integration_id = UUID(batch['integration_id'])

        # Check if already mapped
        existing_count = self.raw_asset_repository.count_by_batch(batch_id)
        if existing_count > 0:
            return {
                'success': False,
                'error': f'Batch already mapped ({existing_count} records exist)',
                'batch_id': str(batch_id),
                'mapped_count': existing_count
            }

        # Get integration with field mapping
        integration = self.integration_repository.get_by_id(integration_id)
        if not integration:
            return {
                'success': False,
                'error': 'Integration not found',
                'batch_id': str(batch_id)
            }

        # Update batch status
        self.batch_repository.update_batch_status(batch_id, 'mapping')

        try:
            # Create field mapper (include transformations for virtual fields)
            field_mapper = FieldMapper(integration.field_mapping, integration.transformations)

            total_raw = self.raw_data_repository.count_records(batch_id)
            logger.info(f"Starting mapping for batch {batch_id}: {total_raw} total raw records")

            # Process records in paginated batches to handle large datasets
            mapped_count = 0
            failed_count = 0
            errors = []
            buffer = []
            buffer_size = 100  # Bulk insert every 100 records
            page_size = 5000
            offset = 0

            while True:
                raw_records = self.raw_data_repository.get_records(batch_id, limit=page_size, offset=offset)
                if not raw_records:
                    break

                for raw_record in raw_records:
                    try:
                        # Apply field mapping
                        mapped_data = field_mapper.map(raw_record['data'])

                        # Add to buffer
                        buffer.append({
                            'batch_id': batch_id,
                            'integration_id': integration_id,
                            'raw_source_id': raw_record['id'],
                            'mapped_data': mapped_data
                        })

                        # Bulk insert when buffer is full
                        if len(buffer) >= buffer_size:
                            self.raw_asset_repository.create_mapped_records_bulk(buffer)
                            mapped_count += len(buffer)
                            buffer = []

                    except Exception as e:
                        logger.error(f"Failed to map record {raw_record['id']}: {e}", exc_info=True)
                        failed_count += 1
                        errors.append(f"Record {raw_record['id']}: {str(e)}")

                logger.info(f"Mapped {mapped_count + len(buffer)}/{total_raw} records so far...")
                offset += page_size

            # Insert remaining records in buffer
            if buffer:
                self.raw_asset_repository.create_mapped_records_bulk(buffer)
                mapped_count += len(buffer)

            # Update batch with mapped count and status
            status = 'completed' if failed_count == 0 else 'partial'
            self.batch_repository.update_mapped_count(batch_id, mapped_count)
            self.batch_repository.update_batch_status(
                batch_id,
                status,
                f"{failed_count} records failed to map" if failed_count > 0 else None
            )

            # Store mapping config hash for stale-mapping detection
            mapping_hash = _compute_mapping_hash(
                integration.field_mapping, integration.transformations
            )
            self.batch_repository.update_mapping_hash(batch_id, mapping_hash)

            logger.info(f"Batch {batch_id} mapping complete: {mapped_count} mapped, {failed_count} failed")

            return {
                'success': True,
                'batch_id': str(batch_id),
                'mapped_count': mapped_count,
                'failed_count': failed_count,
                'errors': errors[:10]  # Limit to first 10 errors
            }

        except Exception as e:
            logger.error(f"Batch mapping failed for {batch_id}: {e}", exc_info=True)

            # Mark batch as failed
            self.batch_repository.fail_batch(batch_id, str(e))

            return {
                'success': False,
                'error': str(e),
                'batch_id': str(batch_id),
                'mapped_count': mapped_count if 'mapped_count' in locals() else 0,
                'failed_count': failed_count if 'failed_count' in locals() else 0
            }

    def map_single_record(self, raw_record_id: int) -> Optional[int]:
        """
        Map a single raw record to raw_asset_data.

        Args:
            raw_record_id: ID from raw_source_data

        Returns:
            ID of created raw_asset_data record, or None if failed
        """
        try:
            # Get raw record
            raw_record = self.raw_data_repository.get_record_by_id(raw_record_id)
            if not raw_record:
                logger.error(f"Raw record {raw_record_id} not found")
                return None

            # Get integration
            integration_id = UUID(raw_record['integration_id'])
            integration = self.integration_repository.get_by_id(integration_id)
            if not integration:
                logger.error(f"Integration {integration_id} not found")
                return None

            # Create field mapper and map record (include transformations for virtual fields)
            field_mapper = FieldMapper(integration.field_mapping, integration.transformations)
            mapped_data = field_mapper.map(raw_record['data'])

            # Store mapped record
            mapped_id = self.raw_asset_repository.create_mapped_record(
                batch_id=UUID(raw_record['batch_id']),
                integration_id=integration_id,
                raw_source_id=raw_record_id,
                mapped_data=mapped_data
            )

            logger.info(f"Mapped record {raw_record_id} → {mapped_id}")
            return mapped_id

        except Exception as e:
            logger.error(f"Failed to map record {raw_record_id}: {e}", exc_info=True)
            return None

    def remap_batch(self, batch_id: UUID) -> Dict[str, Any]:
        """
        Delete existing mapped records and remap batch.

        Used when field_mapping configuration is updated.

        Args:
            batch_id: Batch UUID

        Returns:
            {
                'success': bool,
                'batch_id': str,
                'deleted_count': int,
                'mapped_count': int,
                'failed_count': int
            }
        """
        try:
            # Delete existing mapped records
            deleted_count = self.raw_asset_repository.delete_by_batch(batch_id)

            logger.info(f"Deleted {deleted_count} existing mapped records for batch {batch_id}")

            # Remap the batch
            result = self.map_batch(batch_id)

            # Add deleted count to result
            result['deleted_count'] = deleted_count

            return result

        except Exception as e:
            logger.error(f"Failed to remap batch {batch_id}: {e}", exc_info=True)

            return {
                'success': False,
                'error': str(e),
                'batch_id': str(batch_id)
            }

    def get_unmapped_count(self, integration_id: UUID) -> int:
        """
        Get count of raw records not yet mapped for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Number of unmapped records
        """
        return self.raw_asset_repository.get_unmapped_count(integration_id)

    def get_mapping_stats(self, integration_id: UUID) -> Dict[str, Any]:
        """
        Get comprehensive mapping statistics for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Dictionary with statistics
        """
        stats = self.raw_asset_repository.get_stats(integration_id)

        # Add unmapped count
        stats['unmapped_count'] = self.get_unmapped_count(integration_id)

        # Calculate field coverage
        total = stats['total_mapped_records']
        if total > 0:
            stats['field_coverage'] = {
                'hostname': (stats['records_with_hostname'] / total * 100),
                'ip': (stats['records_with_ip'] / total * 100),
                'mac': (stats['records_with_mac'] / total * 100),
                'sn': (stats['records_with_sn'] / total * 100)
            }
        else:
            stats['field_coverage'] = {
                'hostname': 0.0,
                'ip': 0.0,
                'mac': 0.0,
                'sn': 0.0
            }

        return stats
