# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Mapping Preview Service

Applies field mapping to raw records and validates components
to preview which records would be dropped by the asset generation pipeline.
"""

import logging
from typing import Dict, Any, List, Optional
from uuid import UUID

from src.modules.integrations.repositories import IntegrationRepository
from src.modules.integrations.repositories.batch_repository import BatchRepository
from src.modules.integrations.repositories.raw_data_repository import RawDataRepository
from src.modules.integrations.services.field_mapper import FieldMapper
from src.modules.integrations.services.field_schema import _detect_type
from src.modules.asset_generation.domain.asset_validator import AssetValidator

logger = logging.getLogger(__name__)

# Framework fields in display order
FRAMEWORK_FIELDS_ORDER = [
    'hostname', 'fqdn', 'ip', 'mac', 'sn',
    'os', 'manufacturer', 'model', 'vendor_category',
    'first_time', 'last_time', 'user',
]


class MappingPreviewService:
    """Preview how raw records map to AssetAtlas fields and which would be dropped."""

    def __init__(
        self,
        integration_repository: Optional[IntegrationRepository] = None,
        batch_repository: Optional[BatchRepository] = None,
        raw_data_repository: Optional[RawDataRepository] = None,
    ):
        self.integration_repo = integration_repository or IntegrationRepository()
        self.batch_repo = batch_repository or BatchRepository()
        self.raw_data_repo = raw_data_repository or RawDataRepository()
        self.validator = AssetValidator()

    def get_mapping_preview(
        self, integration_id: str, limit: int = 50, offset: int = 0,
        status: str = 'all',
    ) -> Dict[str, Any]:
        integration = self.integration_repo.get_by_id(integration_id)
        if not integration:
            return {'success': False, 'error': 'Integration not found'}

        field_mapping = integration.field_mapping or {}
        transformations = integration.transformations or {}

        if not field_mapping:
            return {
                'success': True,
                'total_records': 0,
                'filtered_total': 0,
                'dropped_count': 0,
                'columns': [],
                'records': [],
                'error': 'No field mapping configured',
            }

        batch = self.batch_repo.get_latest_completed_batch(UUID(integration_id))
        if not batch:
            return {
                'success': True,
                'batch_id': None,
                'batch_completed_at': None,
                'total_records': 0,
                'filtered_total': 0,
                'dropped_count': 0,
                'columns': [],
                'records': [],
            }

        batch_id = batch['id']

        # Build columns list from mapping (framework fields in order, then extras)
        columns = [f for f in FRAMEWORK_FIELDS_ORDER if f in field_mapping]
        for key in field_mapping:
            if key not in columns:
                columns.append(key)

        # Build source→target reverse mapping for the frontend
        # Virtual (transformed) fields are treated as source fields in their own right
        source_map = {}
        transform_map = {}  # virtual_field → list of raw source fields feeding it

        for target, source in field_mapping.items():
            if isinstance(source, str):
                if source in transformations:
                    # Named transform: the virtual field IS the source
                    source_map[source] = target
                    src_fields = self._extract_transform_source_fields(transformations[source])
                    transform_map[source] = src_fields
                else:
                    source_map[source] = target
            elif isinstance(source, list):
                for s in source:
                    if isinstance(s, str):
                        source_map[s] = target
                        if s in transformations:
                            src_fields = self._extract_transform_source_fields(transformations[s])
                            transform_map[s] = src_fields
            elif isinstance(source, dict) and source.get('type') == 'transform':
                # Inline transform: use target name as virtual field name
                src_fields = self._extract_transform_source_fields(source)
                transform_map[target] = src_fields
                # The raw source fields feed into the transform
                for f in src_fields:
                    source_map[f] = target

        # Map + validate
        mapper = FieldMapper(field_mapping, transformations)
        total_records = self.raw_data_repo.count_records(batch_id)

        if status in ('ok', 'dropped'):
            # The dropped/ok status is computed after mapping + validation, so
            # it can't be pushed into SQL. Scan the whole batch once, collect
            # the matching records for the requested page, and report how many
            # records match so the client can paginate the filtered view.
            dropped_count, field_stats, records, filtered_total = self._scan_batch(
                mapper, batch_id, total_records, columns, source_map, transform_map,
                status=status, limit=limit, offset=offset,
            )
        else:
            # status == 'all': fast path — page straight from the raw records.
            raw_records = self.raw_data_repo.get_records(batch_id, limit, offset)

            records = []
            page_dropped = 0
            for i, raw_rec in enumerate(raw_records):
                payload = raw_rec.get('data') or raw_rec.get('raw_payload') or {}
                mapped = mapper.map(payload)
                dropped, reasons = self._validate_record(mapped)
                if dropped:
                    page_dropped += 1
                records.append(self._build_record(
                    mapper, payload, mapped, offset + i + 1,
                    columns, transform_map, dropped, reasons,
                ))

            # Single pass over all records for both dropped_count and field_stats
            if offset == 0 and len(raw_records) >= total_records:
                # Small batch — already have all records, compute from page data
                dropped_count = page_dropped
                field_stats = self._compute_field_stats_from_records(
                    mapper, raw_records, source_map, transform_map,
                )
            else:
                dropped_count, field_stats, _, _ = self._scan_batch(
                    mapper, batch_id, total_records, columns, source_map, transform_map,
                )
            filtered_total = total_records

        return {
            'success': True,
            'batch_id': str(batch_id),
            'batch_completed_at': str(batch.get('completed_at', '')),
            'total_records': total_records,
            'filtered_total': filtered_total,
            'dropped_count': dropped_count,
            'columns': columns,
            'source_map': source_map,
            'transform_map': transform_map,
            'field_stats': field_stats,
            'records': records,
        }

    def _build_record(
        self, mapper, payload, mapped, row_num, columns, transform_map, dropped, reasons,
    ) -> Dict[str, Any]:
        """Build a preview record dict from an already-mapped payload.

        Must be called immediately after ``mapper.map(payload)`` for this row,
        since it reads the mapper's virtual-field cache for the latest record.
        """
        vf_cache = mapper.get_virtual_fields()
        virtual_fields = {
            vf: vf_cache[vf]
            for vf in transform_map
            if vf_cache.get(vf) is not None
        }
        return {
            'row_num': row_num,
            'raw': payload,
            'virtual_fields': virtual_fields,
            'mapped': {col: mapped.get(col) for col in columns},
            'dropped': dropped,
            'drop_reasons': reasons,
        }

    def _validate_record(self, mapped: Dict[str, Any]) -> tuple:
        reasons = []

        hostname = mapped.get('hostname')
        fqdn = mapped.get('fqdn')
        ip = mapped.get('ip')
        mac = mapped.get('mac')
        sn = mapped.get('sn')

        has_valid_hostname = False
        has_valid_fqdn = False
        has_valid_sn = False
        has_valid_ip = False
        has_valid_physical_mac = False

        def _validate_values(raw, validate_fn, invalid_msg_fn):
            """Validate scalar or list values, return True if any valid."""
            values = raw if isinstance(raw, list) else [raw]
            found_valid = False
            for v in values:
                if not v:
                    continue
                if not validate_fn(v):
                    reasons.append(invalid_msg_fn(v))
                else:
                    found_valid = True
            return found_valid

        if hostname:
            has_valid_hostname = _validate_values(
                hostname,
                lambda h: (AssetValidator.is_valid_component_value(h, 'hostname')
                           and self.validator.is_valid_hostname(h)),
                lambda h: (f'invalid hostname format: {h}'
                           if not AssetValidator.is_valid_component_value(h, 'hostname')
                           else f'hostname rejected: {h} (generic/placeholder/too short)')
            )

        if fqdn:
            has_valid_fqdn = _validate_values(
                fqdn,
                lambda f: AssetValidator.is_valid_component_value(f, 'fqdn'),
                lambda f: f'invalid FQDN format: {f}'
            )

        if sn:
            has_valid_sn = _validate_values(
                sn,
                lambda s: (AssetValidator.is_valid_component_value(str(s), 'sn')
                           and not self.validator.is_sn_suspicious(s)),
                lambda s: (f'invalid serial number format: {s}'
                           if not AssetValidator.is_valid_component_value(str(s), 'sn')
                           else f'suspicious serial number: {s}')
            )

        if ip:
            has_valid_ip = _validate_values(
                ip,
                lambda i: self.validator.is_valid_ip(i),
                lambda i: f'invalid/excluded IP: {i}'
            )

        if mac:
            macs = mac if isinstance(mac, list) else [mac]
            for m in macs:
                if not m:
                    continue
                if not self.validator.is_valid_mac(m):
                    reasons.append(f'invalid MAC: {m}')
                else:
                    is_virtual, vendor = AssetValidator.is_virtual_mac(m)
                    if is_virtual:
                        reasons.append(f'virtual MAC ({vendor}): {m}')
                    else:
                        has_valid_physical_mac = True

        has_persistent = has_valid_hostname or has_valid_fqdn or has_valid_sn or has_valid_physical_mac

        if not has_persistent and not has_valid_ip:
            reasons.append('no identifiers mapped')
            return True, reasons

        if not has_persistent:
            reasons.append('no persistent identifier (only transient: IP/virtual MAC)')
            return True, reasons

        return False, reasons

    @staticmethod
    def _extract_transform_source_fields(transform_def: Dict[str, Any]) -> list:
        """Extract raw source field names from a transformation definition."""
        fields = []
        ops = transform_def.get('operations', [])
        for op in ops:
            if op.get('type') == 'field' and op.get('field'):
                fields.append(op['field'])
            elif op.get('type') == 'coalesce':
                # Coalesce has a list of field names to try
                for f in op.get('fields', []):
                    if isinstance(f, str):
                        fields.append(f)
            elif op.get('type') == 'conditional':
                # Conditional can reference fields in condition and branches
                cond = op.get('condition', {})
                if cond.get('field'):
                    fields.append(cond['field'])
        return fields

    def _scan_batch(
        self, mapper, batch_id, total_records, columns, source_map, transform_map,
        status='all', limit=0, offset=0,
    ):
        """Single pass over all records: compute dropped_count and field_stats together.

        When ``status`` is ``'ok'`` or ``'dropped'``, also collect the matching
        records for the requested ``limit``/``offset`` window (paginating over
        the filtered sequence) and report how many records match the filter.

        Returns ``(dropped_count, field_stats, page_records, filtered_total)``.
        For ``status='all'`` no records are collected and ``filtered_total`` is
        the full batch size.
        """
        CHUNK = 1000
        dropped = 0
        stats = {}
        total_scanned = 0

        want_dropped = status == 'dropped'
        collecting = status in ('ok', 'dropped')
        matched = 0
        page_records: List[Dict[str, Any]] = []

        for chunk_offset in range(0, total_records, CHUNK):
            chunk = self.raw_data_repo.get_records(batch_id, CHUNK, chunk_offset)
            for raw_rec in chunk:
                payload = raw_rec.get('data') or raw_rec.get('raw_payload') or {}
                mapped = mapper.map(payload)
                total_scanned += 1

                is_dropped, reasons = self._validate_record(mapped)
                if is_dropped:
                    dropped += 1

                if collecting and is_dropped == want_dropped:
                    if offset <= matched < offset + limit:
                        page_records.append(self._build_record(
                            mapper, payload, mapped, total_scanned,
                            columns, transform_map, is_dropped, reasons,
                        ))
                    matched += 1

                self._collect_field_paths(payload, '', stats)
                for vf_name, vf_val in mapper.get_virtual_fields().items():
                    if vf_val is not None:
                        if vf_name not in stats:
                            stats[vf_name] = {'count': 0, 'type': 'string', 'freq': {}}
                        stats[vf_name]['count'] += 1
                        self._track_freq(stats[vf_name]['freq'], str(vf_val)[:100])

        field_stats = self._build_field_stats(stats, total_scanned, source_map, transform_map)
        filtered_total = matched if collecting else total_records
        return dropped, field_stats, page_records, filtered_total

    def _compute_field_stats_from_records(self, mapper, raw_records, source_map, transform_map):
        """Compute field stats from already-loaded records (small batch shortcut)."""
        stats = {}
        for raw_rec in raw_records:
            payload = raw_rec.get('data') or raw_rec.get('raw_payload') or {}
            mapper.map(payload)
            self._collect_field_paths(payload, '', stats)
            for vf_name, vf_val in mapper.get_virtual_fields().items():
                if vf_val is not None:
                    if vf_name not in stats:
                        stats[vf_name] = {'count': 0, 'type': 'string', 'freq': {}}
                    stats[vf_name]['count'] += 1
                    self._track_freq(stats[vf_name]['freq'], str(vf_val)[:100])
        return self._build_field_stats(stats, len(raw_records), source_map, transform_map)

    @staticmethod
    def _track_freq(freq_dict, value):
        """Track value frequency."""
        freq_dict[value] = freq_dict.get(value, 0) + 1

    @staticmethod
    def _build_field_stats(stats, total, source_map, transform_map):
        """Convert raw stats dict into sorted field_stats list."""
        result = []
        for path, info in stats.items():
            if info['count'] == 0:
                continue
            freq = info['freq']
            sorted_vals = sorted(freq.items(), key=lambda x: x[1], reverse=True)
            top_values = [{'value': v, 'count': c} for v, c in sorted_vals[:5]]
            rare_candidates = sorted_vals[-3:] if len(sorted_vals) > 5 else []
            rare_values = [{'value': v, 'count': c} for v, c in rare_candidates]

            result.append({
                'path': path,
                'type': info['type'],
                'coverage': round((info['count'] / total) * 100, 1),
                'unique_count': len(freq),
                'top_values': top_values,
                'rare_values': rare_values,
                'mapped_to': source_map.get(path),
                'is_virtual': path in transform_map,
                'transform_sources': transform_map.get(path),
            })

        def sort_key(f):
            if f['mapped_to'] and f['mapped_to'] in FRAMEWORK_FIELDS_ORDER:
                return (0, FRAMEWORK_FIELDS_ORDER.index(f['mapped_to']))
            if f['mapped_to']:
                return (1, f['path'])
            return (2, -f['coverage'], f['path'])
        result.sort(key=sort_key)
        return result

    @staticmethod
    def _collect_field_paths(obj, prefix, stats, depth=0):
        """Recursively collect field paths with type and value frequency."""
        if depth > 6 or not isinstance(obj, dict):
            return
        for key, value in obj.items():
            path = f'{prefix}{key}' if not prefix else f'{prefix}.{key}'
            if value is None:
                stats.setdefault(path, {'count': 0, 'type': 'null', 'freq': {}})
                continue
            if isinstance(value, dict):
                MappingPreviewService._collect_field_paths(value, path, stats, depth + 1)
                continue
            type_name = _detect_type(value)

            if path not in stats:
                stats[path] = {'count': 0, 'type': type_name, 'freq': {}}
            stats[path]['count'] += 1
            if stats[path]['type'] == 'null':
                stats[path]['type'] = type_name
            MappingPreviewService._track_freq(stats[path]['freq'], str(value)[:100])
