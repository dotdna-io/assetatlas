# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/test_mapping_preview_service.py
"""Tests for MappingPreviewService."""

import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.modules.integrations.services.mapping_preview_service import MappingPreviewService


@pytest.fixture
def mock_repos():
    return {
        'integration_repo': MagicMock(),
        'batch_repo': MagicMock(),
        'raw_data_repo': MagicMock(),
    }


@pytest.fixture
def service(mock_repos):
    return MappingPreviewService(
        integration_repository=mock_repos['integration_repo'],
        batch_repository=mock_repos['batch_repo'],
        raw_data_repository=mock_repos['raw_data_repo'],
    )


def _make_integration(field_mapping, transformations=None):
    """Create a mock integration with given mapping."""
    integration = MagicMock()
    integration.field_mapping = field_mapping
    integration.transformations = transformations or {}
    return integration


class TestMappingPreview:
    def test_basic_mapping_with_valid_record(self, service, mock_repos):
        """Record with valid hostname + IP should not be dropped."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({
            'hostname': 'computerName',
            'ip': 'ipAddress',
        })
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 1,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': 1, 'data': {'computerName': 'WKS-001', 'ipAddress': '10.1.1.5'}},
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 1

        result = service.get_mapping_preview(str(integration_id))

        assert result['success'] is True
        assert result['total_records'] == 1
        assert len(result['records']) == 1

        rec = result['records'][0]
        assert rec['mapped']['hostname'] == 'WKS-001'
        assert rec['mapped']['ip'] == '10.1.1.5'
        assert rec['dropped'] is False
        assert rec['drop_reasons'] == []

    def test_record_dropped_no_persistent_identifier(self, service, mock_repos):
        """Record with only IP (no hostname/SN/physical MAC) should be dropped."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({
            'ip': 'ipAddress',
        })
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 1,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': 1, 'data': {'ipAddress': '10.1.1.5'}},
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 1

        result = service.get_mapping_preview(str(integration_id))

        rec = result['records'][0]
        assert rec['dropped'] is True
        assert any('persistent' in r.lower() for r in rec['drop_reasons'])

    def test_record_dropped_invalid_hostname(self, service, mock_repos):
        """Record with generic hostname and no other identifiers should be dropped."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({
            'hostname': 'name',
        })
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 1,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': 1, 'data': {'name': 'iPhone'}},
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 1

        result = service.get_mapping_preview(str(integration_id))

        rec = result['records'][0]
        assert rec['dropped'] is True

    def test_no_batch_returns_empty(self, service, mock_repos):
        """No completed batch should return success with empty records."""
        integration_id = uuid4()
        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({'hostname': 'name'})
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = None

        result = service.get_mapping_preview(str(integration_id))

        assert result['success'] is True
        assert result['total_records'] == 0
        assert result['records'] == []

    def test_pagination(self, service, mock_repos):
        """Limit and offset should be forwarded to repository."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({'hostname': 'name'})
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 100,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': i, 'data': {'name': f'HOST-{i:03d}'}} for i in range(10, 20)
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 100

        result = service.get_mapping_preview(str(integration_id), limit=10, offset=10)

        # First get_records call should be the paginated page fetch
        first_call = mock_repos['raw_data_repo'].get_records.call_args_list[0]
        assert first_call.args == (batch_id, 10, 10)
        assert len(result['records']) == 10
        assert result['total_records'] == 100

    def test_dropped_count_across_full_batch(self, service, mock_repos):
        """dropped_count should reflect the entire batch, not just current page."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({
            'ip': 'ipAddress',
        })
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 3,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': i, 'data': {'ipAddress': f'10.1.1.{i}'}} for i in range(3)
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 3

        result = service.get_mapping_preview(str(integration_id))

        assert result['dropped_count'] == 3

    def test_columns_from_mapping(self, service, mock_repos):
        """columns should list the AssetAtlas target fields from the mapping."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({
            'hostname': 'name',
            'ip': 'addr',
            'sn': 'serial',
        })
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 1,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': 1, 'data': {'name': 'WKS-001', 'addr': '10.1.1.5', 'serial': 'ABC123'}},
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 1

        result = service.get_mapping_preview(str(integration_id))

        assert 'hostname' in result['columns']
        assert 'ip' in result['columns']
        assert 'sn' in result['columns']

    def test_raw_includes_full_payload(self, service, mock_repos):
        """raw should include the full payload for the expanded transpose view."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({
            'hostname': 'name',
        })
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 1,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': 1, 'data': {'name': 'WKS-001', 'extra_field': 'ignored', 'another': 123}},
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 1

        result = service.get_mapping_preview(str(integration_id))

        raw = result['records'][0]['raw']
        assert 'name' in raw
        assert 'extra_field' in raw
        assert 'another' in raw

    def test_source_map_in_response(self, service, mock_repos):
        """source_map should map source field paths to target AssetAtlas fields."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration({
            'hostname': 'computerName',
            'ip': 'network.ipAddress',
            'sn': 'serialNumber',
        })
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 1,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': 1, 'data': {'computerName': 'WKS-001'}},
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 1

        result = service.get_mapping_preview(str(integration_id))

        assert result['source_map'] == {
            'computerName': 'hostname',
            'network.ipAddress': 'ip',
            'serialNumber': 'sn',
        }
        assert result['transform_map'] == {}

    def test_transform_source_fields_in_source_map(self, service, mock_repos):
        """Transformation source fields should appear in source_map pointing to target."""
        integration_id = uuid4()
        batch_id = uuid4()

        mock_repos['integration_repo'].get_by_id.return_value = _make_integration(
            field_mapping={
                'hostname': 'displayName',
                'os': 'dotdna_os',  # references a named transformation
            },
            transformations={
                'dotdna_os': {
                    'operations': [
                        {'type': 'field', 'field': 'operatingSystem'},
                        {'type': 'static', 'value': ' '},
                        {'type': 'field', 'field': 'operatingSystemVersion'},
                    ]
                }
            },
        )
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': 1,
        }
        mock_repos['raw_data_repo'].get_records.return_value = [
            {'id': 1, 'data': {
                'displayName': 'WKS-001',
                'operatingSystem': 'Windows',
                'operatingSystemVersion': '10.0.19045',
            }},
        ]
        mock_repos['raw_data_repo'].count_records.return_value = 1

        result = service.get_mapping_preview(str(integration_id))

        # Virtual field name maps to target, not the raw source fields
        assert result['source_map']['displayName'] == 'hostname'
        assert result['source_map']['dotdna_os'] == 'os'
        assert 'operatingSystem' not in result['source_map']

        # transform_map keyed by virtual field name
        assert result['transform_map'] == {
            'dotdna_os': ['operatingSystem', 'operatingSystemVersion'],
        }

        # Virtual field value should appear in record
        rec = result['records'][0]
        assert rec['virtual_fields']['dotdna_os'] == 'Windows 10.0.19045'
        assert rec['mapped']['os'] == 'Windows 10.0.19045'


class TestMappingPreviewStatusFilter:
    """Server-side status filtering (all/ok/dropped).

    Regression: previously the status filter ran client-side over a single
    server page, so selecting 'Dropped' showed nothing when the dropped
    records were not on the first page — even though the batch had drops.
    """

    @staticmethod
    def _setup(mock_repos, all_records, mapping):
        integration_id = uuid4()
        batch_id = uuid4()
        mock_repos['integration_repo'].get_by_id.return_value = _make_integration(mapping)
        mock_repos['batch_repo'].get_latest_completed_batch.return_value = {
            'id': batch_id,
            'completed_at': '2026-03-18T10:00:00Z',
            'raw_records_count': len(all_records),
        }

        def _get_records(b_id, limit, offset):
            return all_records[offset:offset + limit]

        mock_repos['raw_data_repo'].get_records.side_effect = _get_records
        mock_repos['raw_data_repo'].count_records.return_value = len(all_records)
        return integration_id

    def _records_first_valid_then_dropped(self):
        # 3 valid (have hostname), then 2 dropped (only IP — no persistent id).
        return [
            {'id': 0, 'data': {'name': 'WKS-000', 'ipAddress': '10.0.0.0'}},
            {'id': 1, 'data': {'name': 'WKS-001', 'ipAddress': '10.0.0.1'}},
            {'id': 2, 'data': {'name': 'WKS-002', 'ipAddress': '10.0.0.2'}},
            {'id': 3, 'data': {'ipAddress': '10.0.0.3'}},
            {'id': 4, 'data': {'ipAddress': '10.0.0.4'}},
        ]

    def test_dropped_filter_finds_drops_not_on_first_page(self, service, mock_repos):
        """status='dropped' returns drops from across the batch, not just page 1."""
        integration_id = self._setup(
            mock_repos, self._records_first_valid_then_dropped(),
            {'hostname': 'name', 'ip': 'ipAddress'},
        )

        # Page size 2: the first page holds only valid records, so a
        # client-side filter over that page would (wrongly) show nothing.
        result = service.get_mapping_preview(
            str(integration_id), limit=2, offset=0, status='dropped'
        )

        assert result['filtered_total'] == 2
        assert len(result['records']) == 2
        assert all(r['dropped'] is True for r in result['records'])
        # Whole-batch stats are independent of the active filter.
        assert result['total_records'] == 5
        assert result['dropped_count'] == 2

    def test_ok_filter_excludes_dropped(self, service, mock_repos):
        """status='ok' returns only non-dropped records."""
        integration_id = self._setup(
            mock_repos, self._records_first_valid_then_dropped(),
            {'hostname': 'name', 'ip': 'ipAddress'},
        )

        result = service.get_mapping_preview(
            str(integration_id), limit=10, offset=0, status='ok'
        )

        assert result['filtered_total'] == 3
        assert len(result['records']) == 3
        assert all(r['dropped'] is False for r in result['records'])

    def test_dropped_filter_paginates_over_filtered_set(self, service, mock_repos):
        """limit/offset apply to the filtered sequence, not the raw batch."""
        integration_id = self._setup(
            mock_repos, self._records_first_valid_then_dropped(),
            {'hostname': 'name', 'ip': 'ipAddress'},
        )

        result = service.get_mapping_preview(
            str(integration_id), limit=1, offset=1, status='dropped'
        )

        # Second dropped record (original row 5), not the second batch row.
        assert result['filtered_total'] == 2
        assert len(result['records']) == 1
        assert result['records'][0]['dropped'] is True
        assert result['records'][0]['row_num'] == 5

    def test_all_status_reports_filtered_total_equal_to_total(self, service, mock_repos):
        """Default status='all' leaves pagination total equal to the batch size."""
        integration_id = self._setup(
            mock_repos, self._records_first_valid_then_dropped(),
            {'hostname': 'name', 'ip': 'ipAddress'},
        )

        result = service.get_mapping_preview(str(integration_id), limit=2, offset=0)

        assert result['filtered_total'] == 5
        assert result['total_records'] == 5

    def test_dropped_filter_paginates_across_chunk_boundary(self, service, mock_repos):
        """Filtered counting + page window span _scan_batch's CHUNK=1000 windows.

        2500 records, every other one dropped, with a requested page that
        straddles the 1000-record raw-scan chunk boundary — exercises the
        cross-chunk accumulation of matched/filtered_total/row_num.
        """
        records = []
        for i in range(2500):
            ip = f'10.0.{i // 255}.{i % 255}'
            if i % 2 == 0:
                records.append({'id': i, 'data': {'name': f'WKS-{i:04d}', 'ipAddress': ip}})
            else:
                records.append({'id': i, 'data': {'ipAddress': ip}})  # only IP -> dropped
        integration_id = self._setup(
            mock_repos, records, {'hostname': 'name', 'ip': 'ipAddress'}
        )

        # offset 498 of the dropped set lands on original rows 997..1005, so the
        # 5-record page crosses the 1000-record chunk boundary.
        result = service.get_mapping_preview(
            str(integration_id), limit=5, offset=498, status='dropped'
        )

        assert result['total_records'] == 2500
        assert result['dropped_count'] == 1250
        assert result['filtered_total'] == 1250
        assert all(r['dropped'] is True for r in result['records'])
        assert [r['row_num'] for r in result['records']] == [998, 1000, 1002, 1004, 1006]
