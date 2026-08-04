# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for refactored BaseConnector template method."""
import pytest
from unittest.mock import MagicMock
from src.modules.integrations.base.base_connector import BaseConnector


class FakeConnector(BaseConnector):
    """Minimal connector for testing framework behavior."""
    integration_type = "fake_test"
    display_name = "Fake Test Connector"
    category = "Testing"
    description = "For unit tests only"

    def __init__(self, config, records=None):
        self._test_records = records or []
        super().__init__(config)

    def _collect_records(self, batch_id):
        for record in self._test_records:
            yield record

    def test_connection(self):
        return {"success": True, "message": "OK"}

    def get_schema(self):
        return {"fields": [{"name": "id", "path": "id", "type": "string"}]}

    @classmethod
    def get_config_schema(cls):
        return {"api_key": {"type": "password", "label": "API Key", "required": True}}


class TestFetchDataTemplate:
    def test_injects_metadata(self):
        connector = FakeConnector({}, records=[{"id": "1"}])
        results = list(connector.fetch_data("batch-123"))
        assert len(results) == 1
        assert results[0]["_batch_id"] == "batch-123"
        assert results[0]["_source"] == "fake_test"
        assert "_collected_at" in results[0]

    def test_collected_at_is_timezone_aware(self):
        connector = FakeConnector({}, records=[{"id": "1"}])
        results = list(connector.fetch_data("batch-123"))
        collected = results[0]["_collected_at"]
        assert "+" in collected or collected.endswith("Z")

    def test_applies_data_filters(self):
        connector = FakeConnector(
            {"data_filters": [{"field": "status", "operator": "equals", "value": "active"}]},
            records=[{"id": "1", "status": "active"}, {"id": "2", "status": "inactive"}]
        )
        results = list(connector.fetch_data("batch-123"))
        assert len(results) == 1
        assert results[0]["id"] == "1"

    def test_stats_tracking(self):
        connector = FakeConnector(
            {"data_filters": [{"field": "status", "operator": "equals", "value": "active"}]},
            records=[{"id": "1", "status": "active"}, {"id": "2", "status": "inactive"}]
        )
        list(connector.fetch_data("batch-123"))
        assert connector._stats["total"] == 1
        assert connector._stats["filtered"] == 1

    def test_stats_reset_between_calls(self):
        connector = FakeConnector({}, records=[{"id": "1"}])
        list(connector.fetch_data("batch-1"))
        list(connector.fetch_data("batch-2"))
        assert connector._stats["total"] == 1

    def test_stats_not_set_before_fetch(self):
        """_stats is only created when fetch_data runs, not at construction."""
        connector = FakeConnector({})
        assert not hasattr(connector, '_stats')

    def test_logs_execution_lifecycle(self):
        connector = FakeConnector({}, records=[{"id": "1"}])
        connector.log_execution_start = MagicMock(return_value=1.0)
        connector.log_execution_end = MagicMock()
        connector.log_sync_metrics = MagicMock()
        list(connector.fetch_data("batch-123"))
        connector.log_execution_start.assert_called_once_with("batch-123")
        connector.log_sync_metrics.assert_called_once()
        connector.log_execution_end.assert_called_once()

    def test_logs_failure_on_exception(self):
        class FailingConnector(FakeConnector):
            def _collect_records(self, batch_id):
                raise ConnectionError("API down")

        connector = FailingConnector({})
        connector.log_execution_end = MagicMock()
        with pytest.raises(ConnectionError):
            list(connector.fetch_data("batch-123"))
        connector.log_execution_end.assert_called_once()
        _, kwargs = connector.log_execution_end.call_args
        assert kwargs.get("success") is False
        assert "API down" in kwargs.get("error_message", "")

    def test_fetch_data_is_not_abstract(self):
        assert not getattr(BaseConnector.fetch_data, "__isabstractmethod__", False)

    def test_collect_records_is_abstract(self):
        assert getattr(BaseConnector._collect_records, "__isabstractmethod__", False)


class TestGetSampleData:
    def test_strips_metadata_keys(self):
        connector = FakeConnector({}, records=[{"id": "1", "name": "test"}])
        samples = connector.get_sample_data(limit=5)
        assert len(samples) == 1
        assert "_batch_id" not in samples[0]
        assert "_collected_at" not in samples[0]
        assert "_source" not in samples[0]
        assert "id" in samples[0]

    def test_respects_limit(self):
        connector = FakeConnector({}, records=[{"id": str(i)} for i in range(20)])
        samples = connector.get_sample_data(limit=5)
        assert len(samples) == 5

    def test_returns_empty_on_error(self):
        class FailingConnector(FakeConnector):
            def _collect_records(self, batch_id):
                raise RuntimeError("fail")
        connector = FailingConnector({})
        assert connector.get_sample_data() == []


class TestBuildAuth:
    def test_defaults_to_none(self):
        connector = FakeConnector({})
        assert connector.auth is None

    def test_connector_can_override(self):
        class AuthConnector(FakeConnector):
            def _build_auth(self):
                return MagicMock()
        connector = AuthConnector({})
        assert connector.auth is not None


class TestGetSource:
    def test_default_returns_integration_type(self):
        connector = FakeConnector({})
        assert connector._get_source() == "fake_test"

    def test_override_custom_source(self):
        class CustomSourceConnector(FakeConnector):
            integration_type = "manual_upload"
            def _get_source(self):
                return "manual_upload:csv"
        connector = CustomSourceConnector({}, records=[{"id": "1"}])
        results = list(connector.fetch_data("batch-1"))
        assert results[0]["_source"] == "manual_upload:csv"


class TestDataFilterFlatDottedKeys:
    """Data filters must read flat source keys that literally contain dots.

    SentinelOne PowerQuery returns columns like "device.hostname" as top-level
    keys, so a filter on that field must match the literal key, not traverse a
    non-existent nested object (which would drop every record).
    """

    def _connector(self, filters):
        return FakeConnector({'data_filters': filters})

    def test_equals_matches_flat_dotted_key(self):
        connector = self._connector([
            {'field': 'device.hostname', 'operator': 'equals', 'value': 'DESKTOP-01'}
        ])
        assert connector._should_include_record({'device.hostname': 'DESKTOP-01'}) is True

    def test_is_not_empty_keeps_record_with_flat_dotted_key(self):
        connector = self._connector([
            {'field': 'device.hostname', 'operator': 'is_not_empty'}
        ])
        assert connector._should_include_record({'device.hostname': 'DESKTOP-01'}) is True

    def test_nested_path_filter_still_resolves(self):
        connector = self._connector([
            {'field': 'network.ip', 'operator': 'equals', 'value': '10.0.0.1'}
        ])
        assert connector._should_include_record({'network': {'ip': '10.0.0.1'}}) is True
