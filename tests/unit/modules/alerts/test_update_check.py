# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the gated, air-gap-safe update check.

The air-gapped guarantee is proven by test, not assertion: with the setting on
and the rule enabled, a raising network call must still create nothing and must
not propagate an exception.
"""
from unittest.mock import MagicMock

from src.modules.alerts import update_check as uc
from src.modules.alerts.service import AlertsService
from src.modules.alerts.update_check import UpdateCheckService


def _rule(enabled=True):
    return {'id': 'rule-uc', 'slug': uc.UPDATE_RULE_SLUG, 'enabled': enabled,
            'type': 'operational', 'severity': 'low',
            'name': 'AssetAtlas Update Available'}


def _svc(repo, *, enabled_setting=True, current='2026.27.5', clock=None):
    settings = MagicMock()
    settings.get.return_value = enabled_setting
    kwargs = {'clock': clock} if clock is not None else {}
    return UpdateCheckService(repo, settings, current, **kwargs)


def _boom(*_a, **_k):
    raise ConnectionError('air-gapped')


def test_disabled_setting_resolves_alert_and_makes_no_call(monkeypatch):
    # Setting off → resolve any standing alert (so it can't linger after the
    # toggle), make NO network call, and create nothing.
    monkeypatch.setattr('requests.get', _boom)  # a network attempt would raise
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    _svc(repo, enabled_setting=False).tick()
    repo.resolve_alerts_for_rule.assert_called_once_with('rule-uc')
    repo.create_alert.assert_not_called()


def test_fetch_swallows_network_error(monkeypatch):
    # The raw fetcher must turn ANY network error into None.
    monkeypatch.setattr('requests.get', _boom)
    assert _svc(MagicMock())._fetch_latest_version() is None


def test_tick_offline_end_to_end_is_silent(monkeypatch):
    # Setting on + rule enabled, but the network call raises: nothing is
    # created/updated/resolved and tick() does not raise. This is the air-gap
    # guarantee.
    monkeypatch.setattr('requests.get', _boom)
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = None
    _svc(repo).tick()
    repo.create_alert.assert_not_called()
    repo.update_alert_assets.assert_not_called()
    repo.resolve_alert.assert_not_called()


def test_creates_one_alert_when_newer(monkeypatch):
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = None
    svc = _svc(repo, current='2026.27.5')
    monkeypatch.setattr(svc, '_fetch_latest_version', lambda: '2026.28.1')
    svc.tick()
    repo.create_alert.assert_called_once()


def _tick_newer(repo, monkeypatch):
    """Drive one tick that sees a newer release than the installed version."""
    svc = _svc(repo, current='2026.27.5')
    monkeypatch.setattr(svc, '_fetch_latest_version', lambda: '2026.28.1')
    svc.tick()


def test_created_alert_is_stored_as_system_subject(monkeypatch):
    # This alert is about the install, not a set of assets. create_alert defaults
    # subject_type to 'asset', so omitting it stored an asset-subject row carrying
    # asset_count=0 — which is what made the card read "0 assets matched this rule"
    # over an empty Involved Assets grid (#986).
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = None
    _tick_newer(repo, monkeypatch)
    assert repo.create_alert.call_args.kwargs['subject_type'] == 'system'


def test_created_alert_reason_reads_as_a_system_check(monkeypatch):
    # The regression that would have caught #986: feed the row we store through the
    # same staticmethod GET /api/alerts calls and assert the sentence a user reads.
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = None
    _tick_newer(repo, monkeypatch)

    stored = repo.create_alert.call_args.kwargs
    reason = AlertsService._compute_reason({
        # An omitted kwarg lands on create_alert's own 'asset' default, so this
        # mirrors the row Postgres actually holds either way.
        'subject_type': stored.get('subject_type', 'asset'),
        'asset_count': stored['asset_count'],
        'threshold': stored['threshold'],
    })
    assert reason != "0 assets matched this rule"   # the #986 sentence
    # The version threshold carries no operator, so subject_type='system' falls
    # through to the generic per-type sentence. Asserted, not assumed.
    assert reason == "A system health check raised this alert."


def test_supersede_keeps_the_system_subject(monkeypatch):
    # subject_type sits in update_alert_assets' UPDATE SET clause and defaults to
    # 'asset' there too, so a supersede that omits it silently reverts the row to
    # asset-subject — the card would relapse to "0 assets matched this rule" on the
    # next newer release, days after the create-path fix looked correct (#986).
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = {'id': 'alert-1'}
    _tick_newer(repo, monkeypatch)
    repo.update_alert_assets.assert_called_once()
    assert repo.update_alert_assets.call_args.kwargs['subject_type'] == 'system'
    repo.create_alert.assert_not_called()


def test_resolves_when_versions_match(monkeypatch):
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = {'id': 'alert-1'}
    svc = _svc(repo, current='2026.27.5')
    monkeypatch.setattr(svc, '_fetch_latest_version', lambda: '2026.27.5')
    svc.tick()
    repo.resolve_alert.assert_called_once_with('alert-1')
    repo.create_alert.assert_not_called()


def test_disabled_rule_resolves_and_skips(monkeypatch):
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule(enabled=False)
    svc = _svc(repo)
    fetch = MagicMock()
    monkeypatch.setattr(svc, '_fetch_latest_version', fetch)
    svc.tick()
    repo.resolve_alerts_for_rule.assert_called_once_with('rule-uc')
    fetch.assert_not_called()  # no network call for a disabled rule


def test_caches_within_ttl(monkeypatch):
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = None
    t = [1000.0]
    svc = _svc(repo, current='2026.27.5', clock=lambda: t[0])
    calls = []
    monkeypatch.setattr(svc, '_fetch_latest_version',
                        lambda: (calls.append(1), '2026.28.1')[1])
    svc.tick()          # first tick fetches
    t[0] += 3600        # +1h, well within the cache TTL
    svc.tick()          # cached: must not fetch again
    assert len(calls) == 1


def test_cache_ttl_below_daily_job_interval():
    # The update_check job fires every 24h and stamps the cache a few seconds
    # after each fire; a >=24h TTL would make the next daily fire land inside the
    # window and skip it (48h effective cadence). Keep the TTL strictly below 24h.
    assert uc._CACHE_TTL_SECONDS < 24 * 3600


def test_older_or_equal_does_not_alert_without_existing(monkeypatch):
    repo = MagicMock()
    repo.get_rule_by_slug.return_value = _rule()
    repo.get_active_alert_for_rule.return_value = None
    svc = _svc(repo, current='2026.27.5')
    monkeypatch.setattr(svc, '_fetch_latest_version', lambda: '2026.27.5')
    svc.tick()
    repo.create_alert.assert_not_called()
    repo.resolve_alert.assert_not_called()


def test_module_fetch_latest_version_swallows_error(monkeypatch):
    # The module-level fetcher must turn ANY network error into None (air-gap).
    monkeypatch.setattr('requests.get', _boom)
    assert uc.fetch_latest_version() is None


class _Resp:
    """Minimal stand-in for a requests Response with a JSON body."""

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def test_module_fetch_parses_public_version_manifest(monkeypatch):
    # Regression for "Couldn't reach release server": the private repo made the
    # old GitHub releases API 404. We now read a public {"version": ...} manifest;
    # the fetcher must return that version. (Fails against the old tag_name code.)
    monkeypatch.setattr('requests.get',
                        lambda *a, **k: _Resp({'version': '2026.28.12'}))
    assert uc.fetch_latest_version() == '2026.28.12'


def test_module_fetch_strips_v_prefix_from_manifest(monkeypatch):
    monkeypatch.setattr('requests.get',
                        lambda *a, **k: _Resp({'version': 'v2026.28.12'}))
    assert uc.fetch_latest_version() == '2026.28.12'


def test_module_fetch_missing_version_key_is_none(monkeypatch):
    # A malformed/legacy body without "version" collapses to None (stays silent).
    monkeypatch.setattr('requests.get', lambda *a, **k: _Resp({'tag_name': 'x'}))
    assert uc.fetch_latest_version() is None


def test_module_is_newer_zero_pads_and_compares():
    assert uc.is_newer('2026.28.1', '2026.27.5') is True
    assert uc.is_newer('2026.27.5', '2026.27.5') is False   # equal → not newer
    assert uc.is_newer('2026.27', '2026.27.0') is False     # zero-pad, not newer
    assert uc.is_newer('2026.27.0', '2026.27') is False     # reverse: elided patch must NOT read as newer
    assert uc.is_newer(None, '2026.27.5') is False          # unparseable → False
