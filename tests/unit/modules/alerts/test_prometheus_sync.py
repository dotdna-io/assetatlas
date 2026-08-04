# SPDX-License-Identifier: AGPL-3.0-or-later
"""Boot-time YAML → alert_rules sync."""
import os
from unittest.mock import MagicMock

import pytest

from src.modules.alerts.prometheus_sync import sync_prometheus_rules
from tests.unit.modules.alerts.helpers import get_query_and_params


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def fake_adapter():
    adapter = MagicMock()
    # Default: no existing rows
    adapter.execute_query = MagicMock(return_value=[])
    return adapter


def test_inserts_three_rules_from_two_files(fake_adapter):
    result = sync_prometheus_rules(
        os.path.join(FIXTURES, "prometheus_rules_ok"),
        fake_adapter,
    )
    # system.yml has 2 rules, host.yml has 1 → 3 upserts
    assert result["processed"] == 3
    assert result["removed"] == 0


def test_slug_equals_alertname(fake_adapter):
    sync_prometheus_rules(
        os.path.join(FIXTURES, "prometheus_rules_ok"),
        fake_adapter,
    )
    slugs = set()
    for call in fake_adapter.execute_query.call_args_list:
        q, p = get_query_and_params(call)
        if "INSERT INTO alert_rules" in q and isinstance(p, dict) and "slug" in p:
            slugs.add(p["slug"])
    assert {"system-api-error-rate", "system-scheduler-stalled", "host-disk-full-90"} <= slugs


def test_severity_mapped_from_label(fake_adapter):
    sync_prometheus_rules(
        os.path.join(FIXTURES, "prometheus_rules_ok"),
        fake_adapter,
    )
    seen = {}
    for call in fake_adapter.execute_query.call_args_list:
        q, p = get_query_and_params(call)
        if "INSERT INTO alert_rules" in q and isinstance(p, dict) and "slug" in p:
            seen[p["slug"]] = p["severity"]
    assert seen["system-api-error-rate"] == "high"    # warning → high
    assert seen["system-scheduler-stalled"] == "critical"
    assert seen["host-disk-full-90"] == "critical"


def test_missing_severity_label_maps_to_medium(tmp_path, fake_adapter):
    yaml_file = tmp_path / "no-sev.yml"
    yaml_file.write_text("""
groups:
  - name: t
    rules:
      - alert: rule-without-severity
        expr: up == 0
        annotations: {summary: s, description: d}
""")
    sync_prometheus_rules(str(tmp_path), fake_adapter)
    for call in fake_adapter.execute_query.call_args_list:
        q, p = get_query_and_params(call)
        if "INSERT INTO alert_rules" in q and isinstance(p, dict) and p.get("slug") == "rule-without-severity":
            assert p["severity"] == "medium"
            return
    pytest.fail("rule-without-severity was not upserted")


def test_removes_stale_prometheus_rules(fake_adapter):
    """Rules in DB with source='prometheus' but not in YAML should be removed."""
    fake_adapter.execute_query = MagicMock(side_effect=[
        [{"slug": "system-api-error-rate"},
         {"slug": "system-scheduler-stalled"},
         {"slug": "host-disk-full-90"},
         {"slug": "stale-rule-no-longer-in-yaml"}],
        [], [], [], [], [], [], [], [], [], [],
    ])
    result = sync_prometheus_rules(
        os.path.join(FIXTURES, "prometheus_rules_ok"),
        fake_adapter,
    )
    assert result["removed"] == 1
    found_delete = False
    for call in fake_adapter.execute_query.call_args_list:
        q, p = get_query_and_params(call)
        if "DELETE" in q.upper() and isinstance(p, dict) and p.get("slug") == "stale-rule-no-longer-in-yaml":
            found_delete = True
    assert found_delete, "expected DELETE for stale slug"


def test_malformed_yaml_logged_not_fatal(caplog, fake_adapter):
    """A broken YAML file is logged but does not abort the sync."""
    import logging
    caplog.set_level(logging.WARNING)
    result = sync_prometheus_rules(
        os.path.join(FIXTURES, "prometheus_rules_bad"),
        fake_adapter,
    )
    assert result["processed"] == 0
    assert any("broken.yml" in r.message or "parse" in r.message.lower() or "read" in r.message.lower()
               for r in caplog.records)


def test_empty_directory_returns_zero_counts(tmp_path, fake_adapter):
    result = sync_prometheus_rules(str(tmp_path), fake_adapter)
    assert result == {"processed": 0, "inserted": 0, "updated": 0, "removed": 0}


def test_missing_directory_returns_zero_counts(fake_adapter, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    result = sync_prometheus_rules("/nonexistent/path/for/test", fake_adapter)
    assert result == {"processed": 0, "inserted": 0, "updated": 0, "removed": 0}
    assert any("does not exist" in r.message.lower() or "missing" in r.message.lower()
               for r in caplog.records)
