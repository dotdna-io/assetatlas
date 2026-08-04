# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alertmanager v4 payload parsing."""
import pytest
from pydantic import ValidationError

from src.modules.alerts.prometheus_schemas import (
    AlertmanagerPayload,
    AlertmanagerAlert,
)


def test_parses_minimal_firing_payload():
    payload = AlertmanagerPayload.model_validate({
        "version": "4",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "redis-down", "severity": "critical"},
            "annotations": {"summary": "Redis down", "description": "Redis is unreachable."},
            "startsAt": "2026-04-24T12:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": "abc123",
        }],
    })
    assert payload.version == "4"
    assert len(payload.alerts) == 1
    a = payload.alerts[0]
    assert a.labels["alertname"] == "redis-down"
    assert a.fingerprint == "abc123"


def test_parses_resolved_payload():
    payload = AlertmanagerPayload.model_validate({
        "version": "4",
        "status": "resolved",
        "alerts": [{
            "status": "resolved",
            "labels": {"alertname": "redis-down", "severity": "critical"},
            "annotations": {"summary": "Redis down", "description": "Redis is unreachable."},
            "startsAt": "2026-04-24T12:00:00Z",
            "endsAt": "2026-04-24T12:05:00Z",
            "fingerprint": "abc123",
        }],
    })
    assert payload.alerts[0].status == "resolved"


def test_rejects_missing_required_fields():
    with pytest.raises(ValidationError):
        AlertmanagerPayload.model_validate({"version": "4", "status": "firing"})


def test_rejects_invalid_status():
    with pytest.raises(ValidationError):
        AlertmanagerPayload.model_validate({
            "version": "4",
            "status": "bogus",
            "alerts": [],
        })


def test_ignores_extra_fields_for_forward_compat():
    payload = AlertmanagerPayload.model_validate({
        "version": "4",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "x", "severity": "warning"},
            "annotations": {"summary": "s", "description": "d"},
            "startsAt": "2026-04-24T12:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": "fp",
            "generatorURL": "http://prometheus:9090/graph",  # extra field
        }],
        "groupKey": "{}:{alertname=\"x\"}",  # extra field
    })
    assert payload.alerts[0].labels["alertname"] == "x"
