# SPDX-License-Identifier: AGPL-3.0-or-later
"""A mapped framework field must survive the FieldMapper → raw_asset_data handoff.

FieldMapper.map() returns framework fields as top-level keys and everything
else inside ``extended_fields``; RawAssetRepository persists only the
top-level keys that have a raw_asset_data column, plus ``extended_fields``.
A framework field with no column falls through both — it is mapped, shown
in the mapping preview, and then silently discarded. ``user`` sat that way
from 2026.12.21 until this fix: every SentinelOne/ServiceNow/Intune username
was dropped, and assets.user_field read as empty on every deployment.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.modules.integrations.repositories.raw_asset_repository import RawAssetRepository
from src.modules.integrations.services.field_mapper import FieldMapper

# Data columns of the INSERT in RawAssetRepository.create_mapped_records_bulk,
# in tuple order after (integration_id, raw_source_id, batch_id).
_PERSISTED_COLUMNS = [
    'hostname', 'fqdn', 'ip', 'mac', 'sn', 'os', 'manufacturer', 'model',
    'vendor_category', 'first_time', 'last_time', 'extended_fields',
]

# Source values the type converters accept (ip/mac/timestamps are validated).
_SAMPLE_SOURCE_VALUES = {
    'ip': '10.1.2.3',
    'mac': '00:0C:29:3E:4F:5A',
    'first_time': '2026-11-18T00:00:00Z',
    'last_time': '2026-11-18T00:00:00Z',
}


def _persist(mapped):
    """Run mapped output through the real bulk insert; return the persisted row."""
    conn_manager = MagicMock()
    conn = conn_manager.get_connection.return_value.__enter__.return_value
    conn.cursor.return_value.__enter__.return_value.rowcount = 1

    with patch(
        'src.modules.integrations.repositories.raw_asset_repository.execute_values'
    ) as execute_values:
        RawAssetRepository(conn_manager).create_mapped_records_bulk([{
            'integration_id': 'int-1',
            'raw_source_id': 1,
            'batch_id': 'batch-1',
            'mapped_data': mapped,
        }])

    (row,) = execute_values.call_args.args[2]
    persisted = dict(zip(_PERSISTED_COLUMNS, row[3:]))
    ext = persisted['extended_fields']
    persisted['extended_fields'] = ext.adapted if ext is not None else None
    return persisted


def test_mapped_user_is_persisted_for_the_pipeline():
    """The pipeline lifts extended_fields['user'] into assets.user_field."""
    mapper = FieldMapper({'hostname': 'computerName', 'user': 'lastLoggedInUserName'})
    mapped = mapper.map({'computerName': 'WS-01', 'lastLoggedInUserName': 'julie.auger'})

    assert _persist(mapped)['extended_fields'] == {'user': 'julie.auger'}
    # The mapping preview reads framework fields top-level; keep that working.
    assert mapped['user'] == 'julie.auger'


def test_unmapped_user_adds_nothing_to_extended_fields():
    mapper = FieldMapper({'hostname': 'computerName', 'user': 'lastLoggedInUserName'})
    mapped = mapper.map({'computerName': 'WS-01'})

    assert _persist(mapped)['extended_fields'] is None


@pytest.mark.parametrize('framework_field', FieldMapper.FRAMEWORK_FIELDS)
def test_every_framework_field_reaches_raw_asset_data(framework_field):
    """Adding a framework field without a way to persist it must fail here."""
    value = _SAMPLE_SOURCE_VALUES.get(framework_field, 'value-1')
    mapper = FieldMapper({framework_field: 'src_key'})
    mapped = mapper.map({'src_key': value})

    persisted = _persist(mapped)

    assert any(v is not None for v in persisted.values()), (
        f"'{framework_field}' was mapped but nothing reached raw_asset_data: {persisted}"
    )
