# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chronicle Service - Asset change tracking and event generation.

This module is responsible for:
- Comparing current vs new asset states
- Detecting attribute changes (additions, removals, updates)
- Generating chronicle events
- Writing change events to asset_chronicle table
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Any

import pandas as pd
from uuid import UUID, uuid4

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)

# Causation tag for asset_removed events that are identity consolidations (an
# asset absorbed into another), not genuine departures. Shared with the run
# summary (which reports these as "merged") and the dashboard butterfly (which
# excludes them), so the label lives in one place.
PIPELINE_MERGE_CAUSATION = 'pipeline_merge'


class ChronicleService:
    """
    Service for tracking and recording asset changes over time.

    Compares asset states and generates detailed change events including:
    - Attribute updates (value additions/removals)
    - New asset creation
    - Asset removal
    - Asset re-additions
    """

    # Default fields to exclude from change detection
    # Reason: Exclude operational metadata that changes every run but doesn't represent actual asset changes
    DEFAULT_EXCLUDED_FIELDS = {
        # Original exclusions
        '_key', 'last_time', 'first_time', 'asset',
        'id', 'confidence', 'is_compliant',

        # Operational metadata (updated every run)
        'last_seen',           # Timestamp updated every run
        'ingested_at',         # Timestamp when data was ingested
        'last_generation_run_id',  # Current pipeline run UUID (always changes)
        'mapped_at',           # Timestamp when component mappings were created
        'os_eol_checked_at',   # Timestamp when EOL data was checked
        'updated_at',          # Record update timestamp (DB-managed trigger)
        'batch_id',            # Batch processing identifier
        'integration_id',      # Integration source (shouldn't change but not an asset attribute)

        # Lifecycle tracking fields (managed separately)
        'first_seen',          # Initial discovery timestamp (set once, never changes)
        'missing_since',       # Grace-period absence anchor (#795); not an asset change
        'asset_state',         # Lifecycle state (tracked via lifecycle reconciliation)
        'state_changed_at',    # State transition timestamp (tracked separately)
        'last_state_change',   # Previous state for lifecycle tracking

        # Derived/computed fields that may vary slightly
        'category_confidence',  # Confidence score (may vary slightly with algorithm changes)
        'category_rule_type',   # Rule type used (may change with rule updates)

        # Confidence V2 scoring breakdown (not persisted, used for analysis only)
        'confidence_base',                # Base confidence score before penalties
        'confidence_consistency_penalty', # Consistency penalty multiplier
        'confidence_recency_penalty',     # Recency penalty multiplier (time-dependent)
        'confidence_identifier_cap',      # Identifier quality cap
        'confidence_breakdown',           # JSONB breakdown (persisted but not a meaningful change event)

        # Intermediate pipeline columns (not persisted to assets table)
        'raw_source_id',             # Raw record IDs from pipeline DataFrame (not in DB)
        'ip_primary',                # Primary IP computed during aggregation (not in DB)
        'category_is_overridden',    # Override flag computed during scoring (not persisted)
        'category_best_source',      # Best source for category (pipeline-only, not in DB)

        # Pre-override prediction snapshots. These ARE persisted (#1154 — before it
        # they were built as DataFrame columns and silently dropped by the upsert),
        # but they stay excluded on purpose: the pipeline recomputes its prediction
        # on every run, so diffing them would emit a change event for every asset
        # every run. The user-visible category/subcategory are tracked instead,
        # which is where a real change shows up.
        'category_predicted',        # assets.category_predicted
        'subcategory_predicted',     # assets.subcategory_predicted (migration 143)

        # Enrichment metadata (derived from external sources, not actual asset changes)
        'data_source_category',      # Derived from integration metadata
        'os_enrichment',             # OS enrichment technical metadata

        # EOL date fields are excluded (external source data that changes when synced)
        # but os_eol_status IS tracked because it's a meaningful lifecycle event
        # when an OS transitions from 'supported' to 'end_of_life'
        'os_eol_date',               # EOL date from external database (changes when EOL data updates)
        'os_support_end_date',       # Support end from external database
        'os_extended_support_date',  # Extended support from external database
        'os_is_lts',                 # LTS flag from external database
    }

    def __init__(
        self,
        db_adapter: PostgreSQLAdapter,
        excluded_fields: Optional[Set[str]] = None
    ):
        """
        Initialize chronicle service.

        Args:
            db_adapter: PostgreSQL database adapter
            excluded_fields: Custom set of fields to exclude from comparison
        """
        self.db = db_adapter
        self.excluded_fields = excluded_fields or self.DEFAULT_EXCLUDED_FIELDS
        logger.info("ChronicleService initialized")

    # =========================================================================
    # CHANGE DETECTION
    # =========================================================================

    def compare_and_generate_events(
        self,
        current_assets: Dict[str, Dict[str, Any]],
        new_assets: Dict[str, Dict[str, Any]],
        existing_asset_ids: Optional[List[str]] = None,
        event_time: Optional[datetime] = None,
        generation_run_id: Optional[str] = None,
        provider_map: Optional[Dict[str, Dict[str, str]]] = None,
        merge_pairs: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Compare current and new asset states and generate change events.

        Args:
            current_assets: Dictionary of current assets {asset_id: asset_data}
            new_assets: Dictionary of new assets {asset_id: asset_data}
            existing_asset_ids: List of asset IDs that existed previously (for re-add detection)
            event_time: Timestamp for events (defaults to now)
            generation_run_id: UUID of the pipeline run generating these events
            provider_map: Optional mapping of provider info for source attribution
            merge_pairs: Mapping of {removed_asset_id: surviving_asset_id} from pipeline
                merge resolution. Links removal and update events with shared operation_id.

        Returns:
            List of chronicle event dictionaries
        """
        if event_time is None:
            event_time = datetime.now(timezone.utc)

        if existing_asset_ids is None:
            existing_asset_ids = []

        logger.info(
            f"Comparing assets: {len(current_assets)} current, "
            f"{len(new_assets)} new"
        )

        events = []

        current_ids = set(current_assets.keys())
        new_ids = set(new_assets.keys())
        existing_ids_set = set(existing_asset_ids) if existing_asset_ids else set()

        updated_ids = current_ids & new_ids
        created_ids = new_ids - current_ids
        removed_ids = current_ids - new_ids

        # Process updates (most common case, hot path)
        update_event_index = {}
        if updated_ids:
            excluded = self.excluded_fields
            _normalize = self._normalize_to_list
            for asset_id in updated_ids:
                current = current_assets[asset_id]
                new = new_assets[asset_id]
                event = self._generate_update_event_fast(
                    asset_id, current, new, event_time, generation_run_id,
                    excluded, _normalize, provider_map
                )
                if event:
                    events.append(event)
                    update_event_index[event['asset_id']] = event

        for asset_id in created_ids:
            events.extend(
                self._generate_creation_events(
                    asset_id, new_assets[asset_id], existing_ids_set, event_time, generation_run_id
                )
            )

        # Link merge-related events with shared operation_id
        if merge_pairs is None:
            merge_pairs = {}

        for asset_id in removed_ids:
            surviving_id = merge_pairs.get(asset_id)
            if surviving_id is not None:
                # This removal was caused by a pipeline merge
                op_id = str(uuid4())
                events.extend(
                    self._generate_removal_events(
                        asset_id, current_assets[asset_id], event_time,
                        generation_run_id,
                        operation_id=op_id,
                        causation=PIPELINE_MERGE_CAUSATION
                    )
                )
                # Tag the removal with the surviving asset reference
                events[-1]['related_asset_id'] = surviving_id

                # Link the surviving asset's update event (if it exists).
                # Note: when multiple removed assets merge into the same surviving asset,
                # only the last merge iteration's operation_id and related_asset_id will
                # be set here — earlier merge linkages are overwritten. This is a known
                # limitation of the single-valued related_asset_id field.
                surviving_event = update_event_index.get(surviving_id)
                if surviving_event is not None:
                    surviving_event['operation_id'] = op_id
                    surviving_event['causation'] = PIPELINE_MERGE_CAUSATION
                    surviving_event['related_asset_id'] = asset_id
            else:
                events.extend(
                    self._generate_removal_events(
                        asset_id, current_assets[asset_id], event_time,
                        generation_run_id
                    )
                )

        logger.info(f"Generated {len(events)} chronicle events")
        return events

    def _generate_update_event_fast(
        self,
        asset_id: str,
        current: Dict[str, Any],
        new: Dict[str, Any],
        event_time: datetime,
        generation_run_id: Optional[str],
        excluded: set,
        _normalize,
        provider_map: Optional[Dict[str, Dict[str, str]]]
    ) -> Optional[Dict[str, Any]]:
        """
        Optimized update event generation — returns single event or None.

        Inlines normalization and comparison to reduce function call overhead.
        """
        all_keys = set(current.keys()) | set(new.keys())

        changed_attributes = []
        attribute_changes = {}

        for key in all_keys:
            if key in excluded:
                continue

            old_value = current.get(key)
            new_value = new.get(key)

            # Dict-specific branch: compare dicts directly, skip list
            # normalization (dicts are unhashable and can't be sorted/set-diffed)
            if isinstance(old_value, dict) or isinstance(new_value, dict):
                if self._dicts_equal(old_value, new_value):
                    continue
                changed_attributes.append(key)
                change_entry = {
                    'old': [old_value] if old_value is not None else [],
                    'new': [new_value] if new_value is not None else [],
                    'added': [],
                    'removed': [],
                }
                if provider_map:
                    asset_providers = provider_map.get(asset_id)
                    if asset_providers:
                        source = asset_providers.get(key)
                        if source:
                            change_entry['source'] = source
                attribute_changes[key] = change_entry
                continue

            # Inline _normalize_to_list for hot path
            # old_value normalization
            if old_value is None:
                old_list = []
            elif isinstance(old_value, list):
                old_list = old_value
            else:
                try:
                    if pd.isna(old_value):
                        old_list = []
                    else:
                        old_list = [old_value]
                except (ValueError, TypeError):
                    old_list = [old_value]

            # new_value normalization
            if new_value is None:
                new_list = []
            elif isinstance(new_value, list):
                new_list = new_value
            else:
                try:
                    if pd.isna(new_value):
                        new_list = []
                    else:
                        new_list = [new_value]
                except (ValueError, TypeError):
                    new_list = [new_value]

            # Fast-path: both empty
            if not old_list and not new_list:
                continue

            # Fast-path: single-element comparison (most common)
            if len(old_list) == 1 and len(new_list) == 1:
                if old_list[0] == new_list[0]:
                    continue
                values_added = new_list
                values_removed = old_list
            else:
                # Full comparison
                try:
                    if sorted(old_list) != sorted(new_list):
                        values_added = list(set(new_list) - set(old_list))
                        values_removed = list(set(old_list) - set(new_list))
                    else:
                        continue
                except TypeError:
                    if old_list != new_list:
                        values_added = [v for v in new_list if v not in old_list]
                        values_removed = [v for v in old_list if v not in new_list]
                    else:
                        continue

            changed_attributes.append(key)
            change_entry = {
                'old': old_list,
                'new': new_list,
                'added': values_added,
                'removed': values_removed
            }

            if provider_map:
                asset_providers = provider_map.get(asset_id)
                if asset_providers:
                    source = asset_providers.get(key)
                    if source:
                        change_entry['source'] = source

            attribute_changes[key] = change_entry

        if not changed_attributes:
            return None

        return {
            'asset_id': asset_id,
            'time': event_time,
            'event_type': 'attribute_updated',
            'attribute': None,
            'old_value': None,
            'new_value': None,
            'values_removed': None,
            'values_added': None,
            'generation_run_id': generation_run_id,
            'changed_attributes': changed_attributes,
            'attribute_changes': attribute_changes,
            'full_snapshot': None,
            'operation_id': None,
            'causation': 'scheduled_scan',
            'related_asset_id': None
        }

    def _generate_creation_events(
        self,
        asset_id: str,
        new: Dict[str, Any],
        existing_asset_ids: Set[str],
        event_time: datetime,
        generation_run_id: Optional[str] = None,
        operation_id: Optional[str] = None,
        causation: str = 'scheduled_scan'
    ) -> List[Dict[str, Any]]:
        """
        Generate consolidated event for newly created or re-added asset.

        Args:
            asset_id: Asset identifier
            new: New asset state
            existing_asset_ids: Set of previously existing asset IDs (for re-add detection)
            event_time: Event timestamp
            generation_run_id: UUID of the pipeline run
            operation_id: UUID linking related events
            causation: Why this change occurred

        Returns:
            List with single creation event
        """
        is_readd = asset_id in existing_asset_ids
        event_type = 'asset_readded' if is_readd else 'asset_created'

        # Build full snapshot excluding system fields
        full_snapshot = {}
        for key, value in new.items():
            if key not in self.excluded_fields:
                # Store dicts as-is; wrap other values in lists for consistency
                if isinstance(value, dict):
                    full_snapshot[key] = value
                else:
                    full_snapshot[key] = self._normalize_to_list(value)

        # Create single consolidated creation event
        creation_event = {
            'asset_id': asset_id,
            'time': event_time,
            'event_type': event_type,
            'attribute': None,  # Deprecated
            'old_value': None,  # Deprecated
            'new_value': None,  # Deprecated
            'values_removed': None,  # Deprecated
            'values_added': None,  # Deprecated
            'generation_run_id': generation_run_id,
            'changed_attributes': None,  # Not applicable for creation
            'attribute_changes': None,  # Not applicable for creation
            'full_snapshot': full_snapshot,  # Complete asset state
            'operation_id': operation_id,
            'causation': causation,
            'related_asset_id': None
        }

        return [creation_event]

    def _generate_removal_events(
        self,
        asset_id: str,
        current: Dict[str, Any],
        event_time: datetime,
        generation_run_id: Optional[str] = None,
        operation_id: Optional[str] = None,
        causation: str = 'scheduled_scan'
    ) -> List[Dict[str, Any]]:
        """
        Generate consolidated event for removed asset.

        Creates a SINGLE removal event.

        Args:
            asset_id: Asset identifier
            current: Current asset state (being removed)
            event_time: Event timestamp
            generation_run_id: UUID of the pipeline run
            operation_id: UUID linking related events
            causation: Why this change occurred

        Returns:
            List with single removal event
        """
        # Create single removal event
        removal_event = {
            'asset_id': asset_id,
            'time': event_time,
            'event_type': 'asset_removed',
            'attribute': None,  # Deprecated
            'old_value': None,  # Deprecated
            'new_value': None,  # Deprecated
            'values_removed': None,  # Deprecated
            'values_added': None,  # Deprecated
            'generation_run_id': generation_run_id,
            'changed_attributes': None,
            'attribute_changes': None,
            'full_snapshot': None,  # Could optionally store final state
            'operation_id': operation_id,
            'causation': causation,
            'related_asset_id': None
        }

        return [removal_event]

    # =========================================================================
    # PERSISTENCE
    # =========================================================================

    def write_events(self, events: List[Dict[str, Any]]) -> int:
        """
        Write chronicle events to database.

        Args:
            events: List of chronicle event dictionaries

        Returns:
            Number of events written
        """
        if not events:
            logger.info("No events to write")
            return 0

        total_events = len(events)
        logger.info(f"Writing {total_events} chronicle events to database...")

        def _convert_value(obj):
            """Convert pandas Timestamps to strings and NaN/NaT to None."""
            if obj is None:
                return None
            try:
                if math.isnan(obj):
                    return None
            except (TypeError, ValueError):
                pass
            if hasattr(obj, 'isoformat'):
                return obj.isoformat()
            return obj

        def _convert_list(lst):
            """Convert timestamps in a list."""
            if lst is None:
                return None
            return [_convert_value(item) for item in lst]

        def _convert_change_dict(d):
            """Convert timestamps in attribute_changes dict (known structure)."""
            if d is None:
                return None
            result = {}
            for attr, change in d.items():
                result[attr] = {
                    'old': _convert_list(change.get('old')),
                    'new': _convert_list(change.get('new')),
                    'added': _convert_list(change.get('added')),
                    'removed': _convert_list(change.get('removed')),
                }
                if 'source' in change:
                    result[attr]['source'] = change['source']
            return result

        def _convert_snapshot(snap):
            """Convert timestamps in full_snapshot dict (values are lists or dicts)."""
            if snap is None:
                return None
            result = {}
            for k, v in snap.items():
                if isinstance(v, dict):
                    result[k] = v  # Dict values stored as-is
                else:
                    result[k] = _convert_list(v)
            return result

        # Process events with structure-aware conversion (no recursion)
        processed_events = []
        skipped_count = 0

        for event in events:
            # Convert asset_id to UUID
            asset_id = event['asset_id']
            if isinstance(asset_id, str):
                try:
                    asset_id = UUID(asset_id)
                except (ValueError, AttributeError):
                    logger.warning(f"Invalid asset_id format: {asset_id}, skipping event")
                    skipped_count += 1
                    continue

            processed = {
                'asset_id': asset_id,
                'time': event['time'].isoformat() if hasattr(event['time'], 'isoformat') else event['time'],
                'event_type': event['event_type'],
                'attribute': event.get('attribute'),
                'old_value': _convert_list(event.get('old_value')),
                'new_value': _convert_list(event.get('new_value')),
                'values_removed': _convert_list(event.get('values_removed')),
                'values_added': _convert_list(event.get('values_added')),
                'generation_run_id': event.get('generation_run_id'),
                'changed_attributes': event.get('changed_attributes'),
                'attribute_changes': _convert_change_dict(event.get('attribute_changes')),
                'full_snapshot': _convert_snapshot(event.get('full_snapshot')),
                'operation_id': event.get('operation_id'),
                'causation': event.get('causation'),
                'related_asset_id': event.get('related_asset_id'),
            }
            processed_events.append(processed)

        # Write to database
        self.db.bulk_write_chronicle_events(processed_events)

        # Log accurate statistics
        written_count = len(processed_events)
        logger.info(
            f"Chronicle events: {written_count} written, {skipped_count} skipped "
            f"(invalid asset_id), {total_events} total"
        )

        return written_count

    def compare_and_write(
        self,
        current_assets: Dict[str, Dict[str, Any]],
        new_assets: Dict[str, Dict[str, Any]],
        existing_asset_ids: Optional[List[str]] = None,
        event_time: Optional[datetime] = None,
        generation_run_id: Optional[str] = None,
        provider_map: Optional[Dict[str, Dict[str, str]]] = None
    ) -> int:
        """
        Convenience method to compare assets and write events in one call.

        Args:
            current_assets: Dictionary of current assets
            new_assets: Dictionary of new assets
            existing_asset_ids: List of previously existing asset IDs
            event_time: Event timestamp
            generation_run_id: UUID of the pipeline run

        Returns:
            Number of events written
        """
        events = self.compare_and_generate_events(
            current_assets, new_assets, existing_asset_ids, event_time, generation_run_id,
            provider_map=provider_map
        )
        return self.write_events(events)

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    @staticmethod
    def _normalize_to_list(value: Any) -> List:
        """
        Normalize a value to a list for comparison.

        Treats None, NaN, pd.NA, and pd.NaT as empty (returns []).
        """
        if value is None:
            return []
        try:
            if pd.isna(value):
                return []
        except (ValueError, TypeError):
            pass  # pd.isna() raises on lists, dicts, etc.
        if isinstance(value, list):
            return value
        return [value]

    @staticmethod
    def _dicts_equal(a: Any, b: Any) -> bool:
        """
        Deep equality check for dict values using JSON serialization.

        Handles key ordering differences by using sort_keys.
        Treats two None values as equal; one None and one non-None as unequal.
        """
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        if a == b:
            return True
        try:
            return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        except (TypeError, ValueError):
            return False

    def get_excluded_fields(self) -> Set[str]:
        """
        Get the set of fields excluded from change detection.

        Returns:
            Set of excluded field names
        """
        return self.excluded_fields.copy()

    def add_excluded_field(self, field: str) -> None:
        """
        Add a field to the exclusion set.

        Args:
            field: Field name to exclude
        """
        self.excluded_fields.add(field)
        logger.debug(f"Added excluded field: {field}")

    def remove_excluded_field(self, field: str) -> None:
        """
        Remove a field from the exclusion set.

        Args:
            field: Field name to include in comparisons
        """
        if field in self.excluded_fields:
            self.excluded_fields.remove(field)
            logger.debug(f"Removed excluded field: {field}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about chronicle service.

        Returns:
            Dictionary with service information
        """
        return {
            'excluded_fields': list(self.excluded_fields),
            'excluded_field_count': len(self.excluded_fields),
        }
