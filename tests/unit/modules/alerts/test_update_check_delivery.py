# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound delivery for update-available alerts (#986).

#917 built the delivery framework but hooked only the rule and anomaly enqueue
sites, so "a newer AssetAtlas release is available" — arguably the most useful
thing to push to an operator's inbox — rendered on the Alerts page and reached no
channel, silently. These pin the enqueue to the created branch and pin its copy to
what the card shows. Sibling of tests/unit/modules/pipeline_watchdog/
test_watchdog_delivery.py (#985), which did the same job for the watchdog.
"""
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.modules.alert_delivery.service import AlertDeliveryService
from src.modules.alerts import update_check as uc
from src.modules.alerts.service import AlertsService
from src.modules.alerts.update_check import UpdateCheckService

CREATED_AT = datetime(2026, 7, 17, 9, 14, 0, tzinfo=timezone.utc)


def _rule():
    # Shaped like the row get_rule_by_slug returns: source/type are what
    # AlertDeliveryService._category_for reads to pick the routing category.
    return {'id': 'rule-uc', 'slug': uc.UPDATE_RULE_SLUG, 'enabled': True,
            'type': 'operational', 'severity': 'low', 'source': 'system',
            'name': 'AssetAtlas Update Available'}


@pytest.fixture
def alert_repo():
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {'id': 'alert-uc-1', 'created_at': CREATED_AT}
    return repo


@pytest.fixture
def delivery():
    return MagicMock()


def _svc(repo, monkeypatch, *, delivery_service=None, enabled_setting=True):
    settings = MagicMock()
    settings.get.return_value = enabled_setting
    svc = UpdateCheckService(repo, settings, '2026.27.5',
                             delivery_service=delivery_service)
    monkeypatch.setattr(svc, '_fetch_latest_version', lambda: '2026.28.1')
    return svc


@pytest.fixture
def service(alert_repo, delivery, monkeypatch):
    return _svc(alert_repo, monkeypatch, delivery_service=delivery)


def test_created_branch_enqueues_one_delivery(service, alert_repo, delivery):
    """The whole point of #986: before it, update_check.py had no delivery
    reference at all, so a newer release notified nobody."""
    service.tick()

    delivery.enqueue_for_alert.assert_called_once()
    alert, rule_arg = delivery.enqueue_for_alert.call_args.args
    assert alert['id'] == 'alert-uc-1'
    # Real detect-time from the INSERT, not the caller's clock: created_at is the
    # transaction-start default, which is why create_alert returns it (#984). This
    # call site discarded that return value entirely before #986.
    assert alert['created_at'] == CREATED_AT
    assert rule_arg == _rule()


def test_rule_routes_to_the_operational_category(service, delivery):
    """Routing keys off the rule, not the alert. Checked rather than assumed: an
    operator who routed the operational category to a channel gets this alert
    without configuring anything new."""
    service.tick()

    rule_arg = delivery.enqueue_for_alert.call_args.args[1]
    assert AlertDeliveryService._category_for(rule_arg) == 'operational'


def test_enqueued_alert_matches_the_stored_row(service, alert_repo, delivery):
    """The channel must describe the same alert the card does. Both dicts are hand-
    built from the same rule row, so only a test stops them drifting apart."""
    service.tick()

    stored = alert_repo.create_alert.call_args.kwargs
    sent = delivery.enqueue_for_alert.call_args.args[0]
    for stored_key, sent_key in (
        ('alert_type', 'type'), ('severity', 'severity'), ('title', 'title'),
        ('description', 'description'), ('threshold', 'threshold'),
        ('asset_count', 'asset_count'),
        ('assets_by_category', 'assets_by_category'),
        ('data_sources', 'data_sources'), ('asset_ids', 'asset_ids'),
        ('subject_type', 'subject_type'),
    ):
        assert sent[sent_key] == stored[stored_key], sent_key


def test_enqueued_reason_is_what_the_card_computes(service, delivery):
    """#984's reason pin, extended to this path: the sentence is produced by the
    same staticmethod GET /api/alerts calls, so channel and card cannot disagree."""
    service.tick()

    alert = delivery.enqueue_for_alert.call_args.args[0]
    assert alert['reason'] == AlertsService._compute_reason(alert)
    # subject_type='system' + a version threshold with no operator falls through to
    # the generic per-type sentence — not the "0 assets" line #986 fixed first.
    assert alert['reason'] == AlertsService._generic_summary_reason('system')
    assert alert['reason'] == "A system health check raised this alert."
    assert alert['reason'] != "0 assets matched this rule"


def test_supersede_does_not_re_enqueue(service, alert_repo, delivery):
    """A standing update-available alert takes the supersede path on every later
    tick. Enqueueing there would re-notify every single day for as long as the
    operator postpones the upgrade — created is the one-per-firing-episode
    boundary the other hooks use."""
    alert_repo.get_active_alert_for_rule.return_value = {'id': 'alert-uc-1'}

    service.tick()

    alert_repo.update_alert_assets.assert_called_once()
    alert_repo.create_alert.assert_not_called()
    delivery.enqueue_for_alert.assert_not_called()


def test_enqueue_failure_does_not_break_the_check(service, alert_repo, delivery):
    """A delivery miss must never cost us the alert itself — the row is already
    written by the time we enqueue."""
    delivery.enqueue_for_alert.side_effect = RuntimeError('channel exploded')

    service.tick()  # must not raise

    alert_repo.create_alert.assert_called_once()
    delivery.enqueue_for_alert.assert_called_once()


def test_no_delivery_when_the_insert_returned_no_row(service, alert_repo, delivery,
                                                     caplog):
    """create_alert returns None when the INSERT returned no row: nothing to deliver.

    Asserting only "didn't enqueue, didn't raise" would pass with the guard deleted —
    _dispatch_delivery's own except swallows the None deref either way. The guard's
    real effect is that this stays a quiet non-event instead of being logged as a
    "delivery enqueue failed" error, which would send someone hunting a broken
    channel. That absence is what makes this test able to fail.
    """
    alert_repo.create_alert.return_value = None

    with caplog.at_level(logging.ERROR, logger=uc.__name__):
        service.tick()  # must not raise

    delivery.enqueue_for_alert.assert_not_called()
    assert caplog.records == []


def test_disabled_setting_delivers_nothing(alert_repo, delivery, monkeypatch):
    """system.enable_update_check stays the single gate. It is the one alert source
    that reads a PUBLIC manifest, so an air-gapped operator who leaves the setting
    off must get no alert and therefore no outbound delivery — #986 deliberately
    adds no second setting to opt out of delivery alone."""
    svc = _svc(alert_repo, monkeypatch, delivery_service=delivery,
               enabled_setting=False)

    svc.tick()

    alert_repo.create_alert.assert_not_called()
    delivery.enqueue_for_alert.assert_not_called()


def test_update_check_without_delivery_still_creates(alert_repo, monkeypatch):
    """delivery_service stays optional, so every pre-#986 caller and test keeps
    working untouched — a check with no delivery just creates the alert."""
    _svc(alert_repo, monkeypatch).tick()

    alert_repo.create_alert.assert_called_once()
