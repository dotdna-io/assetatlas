# SPDX-License-Identifier: AGPL-3.0-or-later
"""Partial collection runs surface to the operator (#1334).

A connector whose paginator hits its max_pages cap records per-run
collection warnings (BaseConnector.record_collection_warning). These tests
pin what run_integration does with them:

- warnings present   → last_sync_status='partial' + bounded last_error
                       summary; the batch still completes as 'completed';
                       the result dict carries partial/warnings; the
                       Prometheus emit reports status='partial'.
- no warnings        → unchanged: status='success', error=None.
- non-list attribute → a bare MagicMock connector (collection_warnings is a
                       truthy MagicMock) or an older connector is treated as
                       "no warnings" and never marks a run partial.

The shape mirrors tests/unit/test_metrics_instrumentation.py (mocked repos,
patched advisory lock, connector injected via ConnectorRegistry).
"""

import uuid
from unittest.mock import MagicMock

import src.modules.integrations.services.integration_service as isvc
from src.modules.integrations.services.integration_service import (
    _summarize_collection_warnings,
    _PARTIAL_ERROR_MAX_LENGTH,
    _PARTIAL_ERROR_MAX_WARNINGS,
)


def _service():
    svc = isvc.IntegrationService.__new__(isvc.IntegrationService)  # skip __init__
    svc.repository = MagicMock()
    svc.batch_repository = MagicMock()
    svc.raw_data_repository = MagicMock()
    svc.mapping_service = MagicMock()
    svc.conn_manager = MagicMock(database_url="postgresql://u:p@localhost:1/x")
    return svc


def _enabled_integration():
    integ = MagicMock()
    integ.is_enabled.return_value = True
    integ.name = "Test-Partial"
    integ.effective_connector_type = "custom_http"
    integ.config = {}
    integ.data_filters = []
    integ.field_mapping = None
    return integ


def _patch_lock(monkeypatch):
    lock_conn = MagicMock()
    lock_cur = MagicMock()
    lock_cur.fetchone.return_value = [True]
    lock_conn.cursor.return_value.__enter__.return_value = lock_cur
    monkeypatch.setattr(isvc.psycopg2, "connect", lambda *a, **k: lock_conn)


def _run(monkeypatch, connector):
    svc = _service()
    iid = uuid.uuid4()
    svc.repository.get_by_id.return_value = _enabled_integration()
    svc.batch_repository.create_batch.return_value = "batch-1"
    _patch_lock(monkeypatch)
    monkeypatch.setattr(
        isvc.ConnectorRegistry, "create_connector", lambda *a, **k: connector
    )
    result = svc.run_integration(iid)
    return svc, result


def _connector(warnings, records=({"a": 1}, {"a": 2})):
    connector = MagicMock()
    connector.fetch_data.return_value = iter(records)
    connector.collection_warnings = warnings
    return connector


class TestPartialRunPersistence:
    def test_warnings_mark_run_partial_and_batch_still_completes(self, monkeypatch):
        warnings = [
            "Devices truncated at the 500-page cap — more pages were available.",
            "Users truncated at the 500-page cap — more pages were available.",
        ]
        svc, result = _run(monkeypatch, _connector(warnings))

        assert result["success"] is True          # records WERE collected
        assert result["partial"] is True
        assert result["warnings"] == warnings
        assert result["record_count"] == 2
        assert "incomplete" in result["message"]

        # The batch completes normally — a 'partial' batch status would drop
        # the source from the pipeline's completed-batch eligibility read.
        svc.batch_repository.complete_batch.assert_called_once_with("batch-1", 2)
        svc.batch_repository.fail_batch.assert_not_called()

        svc.repository.update_sync_status.assert_called_once()
        _, kwargs = svc.repository.update_sync_status.call_args
        assert kwargs["status"] == "partial"
        assert kwargs["error"] is not None
        for w in warnings:
            assert w in kwargs["error"]

    def test_no_warnings_keeps_success_path(self, monkeypatch):
        svc, result = _run(monkeypatch, _connector([]))

        assert result["success"] is True
        assert result["partial"] is False
        assert result["warnings"] == []
        assert result["message"] == "Successfully fetched 2 records"

        _, kwargs = svc.repository.update_sync_status.call_args
        assert kwargs["status"] == "success"
        assert kwargs["error"] is None

    def test_bare_mock_connector_is_treated_as_no_warnings(self, monkeypatch):
        """MagicMock().collection_warnings is a truthy MagicMock, not a list —
        a naive truthiness check would mark every such run partial."""
        connector = MagicMock()
        connector.fetch_data.return_value = iter([{"a": 1}])
        svc, result = _run(monkeypatch, connector)

        assert result["success"] is True
        assert result["partial"] is False
        _, kwargs = svc.repository.update_sync_status.call_args
        assert kwargs["status"] == "success"
        assert kwargs["error"] is None

    def test_non_list_attribute_is_treated_as_no_warnings(self, monkeypatch):
        svc, result = _run(monkeypatch, _connector("not a list"))

        assert result["partial"] is False
        _, kwargs = svc.repository.update_sync_status.call_args
        assert kwargs["status"] == "success"
        assert kwargs["error"] is None

    def test_partial_run_emits_partial_metric_status(self, monkeypatch):
        from src.api.utils import metrics as m
        svc = _service()
        iid = uuid.uuid4()
        svc.repository.get_by_id.return_value = _enabled_integration()
        svc.batch_repository.create_batch.return_value = "batch-1"
        _patch_lock(monkeypatch)
        monkeypatch.setattr(
            isvc.ConnectorRegistry,
            "create_connector",
            lambda *a, **k: _connector(["cap hit"]),
        )

        svc.run_integration(iid)

        value = 0.0
        for family in m.integration_executions.collect():
            for s in family.samples:
                if s.name != "assetatlas_integration_executions_total":
                    continue
                if (s.labels.get("integration_id") == str(iid)
                        and s.labels.get("status") == "partial"):
                    value = s.value
        assert value == 1.0


class TestSummarizeCollectionWarnings:
    def test_joins_distinct_warnings_in_order(self):
        out = _summarize_collection_warnings(["b cap", "a cap", "b cap"])
        assert out == "b cap; a cap"

    def test_bounds_count_with_more_suffix(self):
        warnings = [f"warning {i}" for i in range(_PARTIAL_ERROR_MAX_WARNINGS + 3)]
        out = _summarize_collection_warnings(warnings)
        assert "warning 0" in out
        assert f"warning {_PARTIAL_ERROR_MAX_WARNINGS}" not in out
        assert out.endswith("(+3 more)")

    def test_caps_total_length(self):
        out = _summarize_collection_warnings(["x" * 5000])
        assert len(out) <= _PARTIAL_ERROR_MAX_LENGTH
        assert out.endswith("...")

    def test_service_persists_bounded_summary_for_thousands_of_warnings(
        self, monkeypatch
    ):
        # Databricks calls its paginator once per catalog and once per schema —
        # a large Unity Catalog estate can record thousands of warnings.
        warnings = [f"schema {i} truncated at the 500-page cap" for i in range(3000)]
        svc, _ = _run(monkeypatch, _connector(warnings))

        _, kwargs = svc.repository.update_sync_status.call_args
        assert kwargs["status"] == "partial"
        assert len(kwargs["error"]) <= _PARTIAL_ERROR_MAX_LENGTH
        assert "more)" in kwargs["error"]
