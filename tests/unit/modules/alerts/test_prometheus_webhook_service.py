# SPDX-License-Identifier: AGPL-3.0-or-later
"""Webhook service — maps Alertmanager payloads → alerts rows."""
from unittest.mock import MagicMock

import pytest

from src.modules.alerts.prometheus_webhook_service import PrometheusWebhookService
from tests.unit.modules.alerts.helpers import get_query_and_params


# Helpers -----------------------------------------------------------------

def make_payload(alerts):
    return {
        "version": "4",
        "status": alerts[0]["status"] if alerts else "firing",
        "alerts": alerts,
    }


def fire_alert(alertname="redis-down", severity="critical", fingerprint="fp1",
               summary="Redis down", description="Redis is unreachable"):
    return {
        "status": "firing",
        "labels": {"alertname": alertname, "severity": severity, "instance": "redis:6379"},
        "annotations": {"summary": summary, "description": description},
        "startsAt": "2026-04-24T12:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": fingerprint,
    }


def resolve_alert(alertname="redis-down", fingerprint="fp1"):
    return {
        "status": "resolved",
        "labels": {"alertname": alertname, "severity": "critical", "instance": "redis:6379"},
        "annotations": {"summary": "Redis down", "description": "Redis is unreachable"},
        "startsAt": "2026-04-24T12:00:00Z",
        "endsAt": "2026-04-24T12:05:00Z",
        "fingerprint": fingerprint,
    }


@pytest.fixture
def adapter():
    """Mock adapter whose responses can be scripted per test."""
    a = MagicMock()
    a.execute_query = MagicMock(return_value=[])
    return a


# Rule lookup ---------------------------------------------------------------

def test_unknown_alertname_is_skipped_and_counted(adapter):
    adapter.execute_query.return_value = []
    svc = PrometheusWebhookService(adapter)
    result = svc.handle_alertmanager_payload(make_payload([fire_alert("unknown-rule")]))
    assert result["processed"] == 1
    assert result["unknown_alertname"] == 1
    assert result["firing"] == 0


def test_unknown_in_batch_does_not_block_known(adapter):
    call_counter = {"n": 0}
    def side_effect(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 1: return []
        if call_counter["n"] == 2: return [{"id": "rule-1"}]
        if call_counter["n"] == 3: return []
        return []

    adapter.execute_query.side_effect = side_effect
    svc = PrometheusWebhookService(adapter)
    result = svc.handle_alertmanager_payload(make_payload([
        fire_alert("unknown-rule", fingerprint="u"),
        fire_alert("redis-down", fingerprint="r"),
    ]))
    assert result["processed"] == 2
    assert result["unknown_alertname"] == 1
    assert result["firing"] == 1


# Firing path --------------------------------------------------------------

def test_firing_first_time_inserts_new_row(adapter):
    adapter.execute_query.side_effect = [
        [{"id": "rule-1"}],
        [],
        [],
    ]
    svc = PrometheusWebhookService(adapter)
    result = svc.handle_alertmanager_payload(make_payload([fire_alert()]))
    assert result["firing"] == 1
    q, _ = get_query_and_params(adapter.execute_query.call_args_list[-1])
    assert "INSERT" in q.upper()


def test_firing_duplicate_updates_existing(adapter):
    adapter.execute_query.side_effect = [
        [{"id": "rule-1"}],
        [{"id": "alert-1"}],
        [],
    ]
    svc = PrometheusWebhookService(adapter)
    result = svc.handle_alertmanager_payload(make_payload([fire_alert()]))
    assert result["firing"] == 1
    q, _ = get_query_and_params(adapter.execute_query.call_args_list[-1])
    assert "UPDATE" in q.upper()
    assert "updated_at" in q.lower()


# Resolved path ------------------------------------------------------------

def test_resolved_with_active_row_flips_status(adapter):
    adapter.execute_query.side_effect = [
        [{"id": "rule-1"}],
        [],
    ]
    svc = PrometheusWebhookService(adapter)
    result = svc.handle_alertmanager_payload(make_payload([resolve_alert()]))
    assert result["resolved"] == 1
    q, _ = get_query_and_params(adapter.execute_query.call_args_list[-1])
    assert "status" in q.lower()
    assert "resolved" in q.lower()


def test_resolved_without_active_row_is_noop(adapter):
    adapter.execute_query.side_effect = [
        [{"id": "rule-1"}],
        [],
    ]
    svc = PrometheusWebhookService(adapter)
    result = svc.handle_alertmanager_payload(make_payload([resolve_alert()]))
    assert result["resolved"] == 1


# Severity mapping ---------------------------------------------------------

@pytest.mark.parametrize("label, expected", [
    ("critical", "critical"),
    ("warning", "high"),
    ("info", "low"),
    ("page", "medium"),
    (None, "medium"),
])
def test_severity_mapping(adapter, label, expected):
    adapter.execute_query.side_effect = [
        [{"id": "rule-1"}],
        [],
        [],
    ]
    labels = {"alertname": "t", "instance": "x"}
    if label is not None:
        labels["severity"] = label
    payload = {
        "version": "4",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": labels,
            "annotations": {"summary": "s", "description": "d"},
            "startsAt": "2026-04-24T12:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": "fp",
        }],
    }
    svc = PrometheusWebhookService(adapter)
    svc.handle_alertmanager_payload(payload)
    _, params = get_query_and_params(adapter.execute_query.call_args_list[-1])
    assert params["severity"] == expected


# Malformed payload --------------------------------------------------------

def test_malformed_payload_raises_validation(adapter):
    from src.api.middleware.error_handler import ValidationError
    svc = PrometheusWebhookService(adapter)
    with pytest.raises(ValidationError):
        svc.handle_alertmanager_payload({"version": "4"})
