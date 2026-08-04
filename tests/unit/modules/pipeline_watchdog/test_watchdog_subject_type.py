# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test that pipeline watchdog alerts are tagged with subject_type='system'."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.modules.pipeline_watchdog.domain import CheckResult, ScheduleSettings
from src.modules.pipeline_watchdog.service import PipelineWatchdogService


@pytest.fixture
def alert_repo():
    return MagicMock()


@pytest.fixture
def watchdog_repo():
    repo = MagicMock()
    repo.get_pipeline_schedule_settings.return_value = ScheduleSettings(
        enabled=False, mode="interval", interval_hours=24,
        cron="0 0 * * *", timezone="UTC",
    )
    repo.get_max_expected_duration_minutes.return_value = 240
    repo.last_successful_completed_at.return_value = None
    repo.last_finished_run.return_value = None
    repo.stuck_runs.return_value = []
    repo.recover_orphaned_pending_runs.return_value = 0
    repo.recover_orphaned_running_runs.return_value = 0
    return repo


@pytest.fixture
def service(watchdog_repo, alert_repo):
    return PipelineWatchdogService(
        watchdog_repo=watchdog_repo, alert_repo=alert_repo,
    )


def _rule(slug, rule_id=None, enabled=True):
    return {
        "id": rule_id or str(uuid4()),
        "slug": slug,
        "enabled": enabled,
    }


def _firing(check_type, severity="high"):
    return CheckResult(
        check_type=check_type, should_fire=True, severity=severity,
        title="t", description="d", threshold={"k": "v"},
    )


def test_watchdog_fire_tags_system_subject(service, alert_repo):
    """Verify that watchdog alerts are created with subject_type='system'."""
    rule = _rule("pipeline-stale")
    alert_repo.get_rule_by_slug.return_value = rule
    alert_repo.get_active_alert_for_rule.return_value = None
    alert_repo.create_alert.return_value = {
        "id": "alert-abc", "created_at": datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}

    service._apply_result("stale", _firing("stale", severity="critical"))

    alert_repo.create_alert.assert_called_once()
    kwargs = alert_repo.create_alert.call_args.kwargs
    assert kwargs["subject_type"] == "system"


def test_watchdog_still_firing_keeps_system_subject(service, alert_repo):
    """A watchdog alert that stays firing across ticks takes the update path.
    update_alert_assets defaults subject_type to 'asset', so without an explicit
    subject_type='system' the still-firing update silently reverts the alert to
    'asset' — reintroducing the empty Involved-Assets grid this feature removes."""
    rule = _rule("pipeline-stale")
    alert_repo.get_rule_by_slug.return_value = rule
    alert_repo.get_active_alert_for_rule.return_value = {"id": "alert-abc"}

    service._apply_result("stale", _firing("stale", severity="critical"))

    alert_repo.create_alert.assert_not_called()
    alert_repo.update_alert_assets.assert_called_once()
    kwargs = alert_repo.update_alert_assets.call_args.kwargs
    assert kwargs["subject_type"] == "system"
