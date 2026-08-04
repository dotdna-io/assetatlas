# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound delivery for pipeline-watchdog alerts (#985).

#917 built the delivery framework but hooked only the rule and anomaly enqueue
sites, so watchdog alerts — pipeline stalled, run failed — rendered on the Alerts
page and reached no channel, silently. These pin the enqueue to the created
branch and pin its copy to what the card shows.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.modules.alerts.service import AlertsService
from src.modules.pipeline_watchdog.domain import CheckResult
from src.modules.pipeline_watchdog.service import PipelineWatchdogService

CREATED_AT = datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)


@pytest.fixture
def alert_repo():
    repo = MagicMock()
    repo.create_alert.return_value = {"id": "alert-abc", "created_at": CREATED_AT}
    return repo


@pytest.fixture
def delivery():
    return MagicMock()


@pytest.fixture
def service(alert_repo, delivery):
    return PipelineWatchdogService(
        watchdog_repo=MagicMock(), alert_repo=alert_repo,
        delivery_service=delivery,
    )


def _rule(slug="pipeline-stale"):
    # Shaped like the row get_rule_by_slug returns: source/type are what
    # AlertDeliveryService._category_for reads to pick the routing category.
    return {
        "id": str(uuid4()), "slug": slug, "name": "Pipeline stale",
        "enabled": True, "type": "operational", "source": "system",
    }


def _firing(check_type="stale", severity="critical"):
    return CheckResult(
        check_type=check_type, should_fire=True, severity=severity,
        title="Asset generation pipeline is stale",
        description="Last successful run completed 90 minutes ago.",
        threshold={"expected_cadence_seconds": 1800, "gap_seconds": 5400},
    )


def test_created_branch_enqueues_one_delivery(service, alert_repo, delivery):
    """The whole point of #985: before it, the watchdog had no delivery reference
    at all, so a stalled pipeline notified nobody."""
    rule = _rule()
    alert_repo.get_rule_by_slug.return_value = rule
    alert_repo.get_active_alert_for_rule.return_value = None

    service._apply_result("stale", _firing())

    delivery.enqueue_for_alert.assert_called_once()
    alert, rule_arg = delivery.enqueue_for_alert.call_args.args
    assert alert["id"] == "alert-abc"
    # Real detect-time from the INSERT, not the caller's clock: created_at is the
    # transaction-start default, which is why create_alert returns it (#984).
    assert alert["created_at"] == CREATED_AT
    # The rule row carries the routing key; source='system' + type='operational'
    # sends this to the 'operational' category.
    assert rule_arg is rule


def test_enqueued_alert_matches_the_stored_row(service, alert_repo, delivery):
    """The channel must describe the same alert the card does. Both dicts are hand-
    built from one CheckResult, so only a test stops them drifting apart."""
    alert_repo.get_rule_by_slug.return_value = _rule()
    alert_repo.get_active_alert_for_rule.return_value = None

    service._apply_result("stale", _firing())

    stored = alert_repo.create_alert.call_args.kwargs
    sent = delivery.enqueue_for_alert.call_args.args[0]
    for stored_key, sent_key in (
        ("alert_type", "type"), ("severity", "severity"), ("title", "title"),
        ("description", "description"), ("threshold", "threshold"),
        ("asset_count", "asset_count"),
        ("assets_by_category", "assets_by_category"),
        ("data_sources", "data_sources"), ("asset_ids", "asset_ids"),
        ("subject_type", "subject_type"),
    ):
        assert sent[sent_key] == stored[stored_key], sent_key


def test_enqueued_reason_is_what_the_card_computes(service, alert_repo, delivery):
    """#984's reason pin, extended to this path: the sentence is produced by the
    same staticmethod GET /api/alerts calls, so channel and card cannot disagree."""
    alert_repo.get_rule_by_slug.return_value = _rule()
    alert_repo.get_active_alert_for_rule.return_value = None

    service._apply_result("stale", _firing())

    alert = delivery.enqueue_for_alert.call_args.args[0]
    assert alert["reason"] == AlertsService._compute_reason(alert)
    # subject_type='system' + a threshold with no operator falls through to the
    # generic per-type sentence. Asserted, not assumed.
    assert alert["reason"] == AlertsService._generic_summary_reason("system")
    assert alert["reason"] == "A system health check raised this alert."


def test_still_firing_does_not_re_enqueue(service, alert_repo, delivery):
    """A standing watchdog alert re-checks every 5 minutes and takes the update
    path. Enqueueing there would re-notify on every tick for as long as the
    pipeline stays broken — created is the one-per-firing-episode boundary."""
    alert_repo.get_rule_by_slug.return_value = _rule()
    alert_repo.get_active_alert_for_rule.return_value = {"id": "alert-abc"}

    service._apply_result("stale", _firing())

    alert_repo.update_alert_assets.assert_called_once()
    alert_repo.create_alert.assert_not_called()
    delivery.enqueue_for_alert.assert_not_called()


def test_enqueue_failure_does_not_break_the_check(service, alert_repo, delivery):
    """A delivery miss must never cost us the alert itself — the row is already
    written by the time we enqueue."""
    alert_repo.get_rule_by_slug.return_value = _rule()
    alert_repo.get_active_alert_for_rule.return_value = None
    delivery.enqueue_for_alert.side_effect = RuntimeError("channel exploded")

    service._apply_result("stale", _firing())  # must not raise

    alert_repo.create_alert.assert_called_once()
    delivery.enqueue_for_alert.assert_called_once()


def test_watchdog_without_delivery_still_fires(alert_repo):
    """delivery_service stays optional, so every pre-#985 caller and test keeps
    working untouched — a watchdog with no delivery just creates the alert."""
    service = PipelineWatchdogService(
        watchdog_repo=MagicMock(), alert_repo=alert_repo,
    )
    alert_repo.get_rule_by_slug.return_value = _rule()
    alert_repo.get_active_alert_for_rule.return_value = None

    service._apply_result("stale", _firing())

    alert_repo.create_alert.assert_called_once()
