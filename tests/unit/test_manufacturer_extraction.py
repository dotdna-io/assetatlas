# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test manufacturer extraction from modelName in SentinelOne connector.

SentinelOne sometimes embeds the manufacturer in the modelName field as
"MANUFACTURER - MODEL" (e.g., "LENOVO - 21K6S0AP00"). The connector's
_normalize_record() should split these into modelName_manufacturer and
modelName_model fields while preserving the original modelName.

Relates to #285.
"""
import logging

import pytest
from unittest.mock import patch

from src.modules.integrations.connectors.sentinelone_connector import (
    SentinelOneConnector,
)


class TestManufacturerExtraction:
    """Manufacturer extraction from modelName in _extract_manufacturer_from_model()."""

    def test_extracts_when_pattern_present(self):
        """'LENOVO - 21K6S0AP00' produces extracted fields."""
        record = {'modelName': 'LENOVO - 21K6S0AP00'}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert record['modelName'] == 'LENOVO - 21K6S0AP00'
        assert record['modelName_manufacturer'] == 'LENOVO'
        assert record['modelName_model'] == '21K6S0AP00'

    def test_no_pattern_no_extraction(self):
        """Plain model string without ' - ' produces no extracted fields."""
        record = {'modelName': 'ThinkPad X1 Carbon'}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert record['modelName'] == 'ThinkPad X1 Carbon'
        assert 'modelName_manufacturer' not in record
        assert 'modelName_model' not in record

    def test_multiple_dashes_splits_on_first(self):
        """'HP - ProBook 450 - G8' splits on the first ' - ' only."""
        record = {'modelName': 'HP - ProBook 450 - G8'}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert record['modelName'] == 'HP - ProBook 450 - G8'
        assert record['modelName_manufacturer'] == 'HP'
        assert record['modelName_model'] == 'ProBook 450 - G8'

    def test_whitespace_trimmed(self):
        """Extra whitespace around parts is stripped."""
        record = {'modelName': '  LENOVO  -  ThinkPad X1  '}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert record['modelName_manufacturer'] == 'LENOVO'
        assert record['modelName_model'] == 'ThinkPad X1'

    def test_empty_model_after_split_skipped(self):
        """'LENOVO - ' (empty model part) should not extract."""
        record = {'modelName': 'LENOVO - '}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert 'modelName_manufacturer' not in record
        assert 'modelName_model' not in record

    def test_empty_manufacturer_after_split_skipped(self):
        """' - 21K6S0AP00' (empty manufacturer part) should not extract."""
        record = {'modelName': ' - 21K6S0AP00'}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert 'modelName_manufacturer' not in record
        assert 'modelName_model' not in record

    def test_null_modelname(self):
        """None modelName is a no-op."""
        record = {'modelName': None}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert 'modelName_manufacturer' not in record

    def test_missing_modelname(self):
        """Record without modelName key is a no-op."""
        record = {'computerName': 'pc01'}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert 'modelName_manufacturer' not in record

    def test_vmware_pattern(self):
        """'VMware, Inc. - VMware7,1' handles commas in manufacturer."""
        record = {'modelName': 'VMware, Inc. - VMware7,1'}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert record['modelName_manufacturer'] == 'VMware, Inc.'
        assert record['modelName_model'] == 'VMware7,1'

    def test_microsoft_vm_pattern(self):
        """'Microsoft Corporation - Virtual Machine' common pattern."""
        record = {'modelName': 'Microsoft Corporation - Virtual Machine'}
        SentinelOneConnector._extract_manufacturer_from_model(record)
        assert record['modelName_manufacturer'] == 'Microsoft Corporation'
        assert record['modelName_model'] == 'Virtual Machine'


@pytest.fixture
def connector():
    """Create a SentinelOneConnector with minimal config (no real API calls)."""
    with patch(
        'src.modules.integrations.connectors.sentinelone_connector.APIKeyAuth'
    ):
        return SentinelOneConnector({
            'url': 'https://test.sentinelone.net',
            'api_token': 'fake-token',
        })


class TestNormalizeRecordExtraction:
    """Verify extraction runs as part of _normalize_record()."""

    def test_normalize_record_extracts_manufacturer(self, connector):
        """_normalize_record() produces extracted fields on output records."""
        record = {
            'computerName': 'pc01',
            'modelName': 'LENOVO - 21K6S0AP00',
            'manufacturer': None,
        }
        results = connector._normalize_record(record)
        assert len(results) >= 1
        assert results[0]['modelName_manufacturer'] == 'LENOVO'
        assert results[0]['modelName_model'] == '21K6S0AP00'
        assert results[0]['modelName'] == 'LENOVO - 21K6S0AP00'

    def test_normalize_record_no_pattern(self, connector):
        """_normalize_record() does not add extracted fields when no pattern."""
        record = {
            'computerName': 'pc02',
            'modelName': 'ThinkPad X1 Carbon',
            'manufacturer': 'LENOVO',
        }
        results = connector._normalize_record(record)
        assert 'modelName_manufacturer' not in results[0]
        assert 'modelName_model' not in results[0]
