# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from src.modules.alert_delivery.channels import registry
from src.modules.alert_delivery.channels.base import DeliveryResult
from src.modules.alert_delivery.channels.log_channel import LogChannel
from src.modules.alert_delivery.payload import build_alert_payload


def test_log_channel_registered_and_sends_ok():
    ch = registry.get('log')
    assert isinstance(ch, LogChannel)
    res = ch.send({}, {'alert_id': 'a1', 'title': 'x'})
    assert isinstance(res, DeliveryResult) and res.ok is True


def test_log_channel_simulate_fail_is_retriable():
    res = registry.get('log').send({'simulate': 'fail'}, {'alert_id': 'a1'})
    assert res.ok is False and res.retriable is True and res.error


def test_log_channel_simulate_permanent_is_not_retriable():
    res = registry.get('log').send({'simulate': 'fail_permanent'}, {'alert_id': 'a1'})
    assert res.ok is False and res.retriable is False


def test_registry_unknown_type_raises():
    with pytest.raises(registry.UnknownChannelType):
        registry.get('nope')


def test_log_channel_has_no_sensitive_fields():
    # The dummy carries no secrets; real channels declare their own.
    assert registry.get('log').sensitive_fields == ()


def test_build_alert_payload_has_stable_keys():
    alert = {
        'id': 'alert-1', 'type': 'compliance', 'severity': 'high',
        'title': 'Disk low', 'description': 'many hosts', 'asset_count': 3,
        'assets_by_category': {'Server': 3}, 'data_sources': ['SentinelOne'],
        'asset_ids': ['a', 'b', 'c'], 'threshold': {'condition': 'exceeds', 'value': 2},
        'reason': '3 assets — above the threshold of 2', 'pipeline_run_id': 'run-9',
        'created_at': '2026-07-07T00:00:00+00:00',
    }
    rule = {'slug': 'disk-low', 'name': 'Disk low'}
    p = build_alert_payload(alert, rule)
    assert p['alert_id'] == 'alert-1'
    assert p['rule_slug'] == 'disk-low' and p['rule_name'] == 'Disk low'
    assert p['severity'] == 'high' and p['asset_count'] == 3
    assert p['asset_ids'] == ['a', 'b', 'c']
    # No 'subject_type'/'subjects' keys on the input (e.g. the anomaly-detection
    # dispatch path never sets them) -> default to asset-subject, no typed list.
    assert p['subject_type'] == 'asset' and p['subjects'] is None
    assert p['source'] == 'system'
    assert set(p) == {
        'alert_id', 'rule_slug', 'rule_name', 'type', 'severity', 'title',
        'description', 'asset_count', 'assets_by_category', 'data_sources',
        'asset_ids', 'threshold', 'reason', 'pipeline_run_id', 'created_at',
        'subject_type', 'subjects', 'assets', 'evidence', 'source',
    }


def test_build_alert_payload_caps_asset_ids():
    alert = {'id': 'a', 'asset_ids': [str(i) for i in range(250)], 'severity': 'low'}
    p = build_alert_payload(alert, {'slug': 's', 'name': 'n'}, max_asset_ids=100)
    assert len(p['asset_ids']) == 100


def test_build_alert_payload_includes_subject_type_and_subjects():
    # Typed-list alert (e.g. sync-failures) — spec §7: the delivery payload must
    # carry subject_type/subjects so channels can render non-asset alerts.
    alert = {
        'id': 'alert-2', 'severity': 'high', 'subject_type': 'integration',
        'subjects': [{'id': 'int-1', 'label': 'CrowdStrike', 'sublabel': 'EDR · sync failed'}],
    }
    rule = {'slug': 'sync-failures', 'name': 'Sync failures'}
    p = build_alert_payload(alert, rule)
    assert p['subject_type'] == 'integration'
    assert p['subjects'] == [
        {'id': 'int-1', 'label': 'CrowdStrike', 'sublabel': 'EDR · sync failed'},
    ]


def test_build_alert_payload_carries_assets_evidence_and_source():
    alert = {
        'id': 'alert-1', 'type': 'security', 'severity': 'high',
        'title': 'Coverage loss', 'description': 'd', 'asset_count': 2,
        'assets_by_category': {'Server': 2}, 'data_sources': ['SentinelOne'],
        'asset_ids': ['a', 'b'],
        'assets': [{'id': 'a', 'label': 'web-prod-01'}, {'id': 'b', 'label': 'db-prod-02'}],
        'evidence': {'narrative': 'n', 'observed': 2, 'typical': 0,
                     'technical': {'z': 3.1}},
        'threshold': {}, 'reason': 'r', 'pipeline_run_id': None,
        'created_at': '2026-07-16T14:32:05+00:00',
    }
    p = build_alert_payload(alert, {'slug': 's', 'name': 'n', 'source': 'system'})
    assert p['assets'] == [{'id': 'a', 'label': 'web-prod-01'},
                           {'id': 'b', 'label': 'db-prod-02'}]
    assert p['evidence'] == {'narrative': 'n', 'observed': 2, 'typical': 0,
                             'technical': {'z': 3.1}}
    assert p['source'] == 'system'
    assert set(p) == {
        'alert_id', 'rule_slug', 'rule_name', 'type', 'severity', 'title',
        'description', 'asset_count', 'assets_by_category', 'data_sources',
        'asset_ids', 'threshold', 'reason', 'pipeline_run_id', 'created_at',
        'subject_type', 'subjects', 'assets', 'evidence', 'source',
    }


def test_build_alert_payload_strips_raw_uuid_evidence_keys():
    """evidence['labels'] and evidence['sample_asset_ids'] are raw-UUID lists, which
    #979's copy contract keeps out of anything an operator reads, and both are
    redundant with `assets` — which names the same entities with labels, losing no
    identity. Not a size guard: every detector caps its asset list at 25 before either
    key is built, so both are already small."""
    alert = {
        'id': 'a1', 'asset_ids': ['a'], 'assets': [{'id': 'a', 'label': 'web-01'}],
        'evidence': {
            'narrative': 'n', 'observed': 1, 'sample': [{'id': 'a', 'label': 'web-01'}],
            'labels': {'a': 'web-01'},
            'sample_asset_ids': ['a'],
        },
    }
    p = build_alert_payload(alert, {})
    assert 'labels' not in p['evidence']
    assert 'sample_asset_ids' not in p['evidence']
    # Everything bounded still travels — the card shows these.
    assert p['evidence']['narrative'] == 'n'
    assert p['evidence']['sample'] == [{'id': 'a', 'label': 'web-01'}]


def test_build_alert_payload_evidence_absent_is_none():
    """Rule alerts have no evidence at all — the key must exist and be null, not
    absent, so the webhook envelope keeps a stable shape."""
    p = build_alert_payload({'id': 'a1', 'asset_ids': []}, {})
    assert p['evidence'] is None
    assert p['assets'] == []


def test_build_alert_payload_maps_user_rule_source_to_custom():
    """Mirrors the UI's 'user' -> 'custom' mapping (alerts/service.py:347-348) so a
    receiver sees the same badge the card shows."""
    p = build_alert_payload({'id': 'a1'}, {'source': 'user'})
    assert p['source'] == 'custom'


def test_build_alert_payload_serializes_created_at_to_iso():
    """A raw datetime breaks the JSONB enqueue and webhook's json.dumps, and
    syslog._timestamp requires a whitespace-free str or it silently falls back to
    send-time."""
    from datetime import datetime, timezone
    p = build_alert_payload(
        {'id': 'a1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}, {})
    assert p['created_at'] == '2026-07-16T14:32:05+00:00'
    assert isinstance(p['created_at'], str)


def test_build_alert_payload_caps_assets_with_asset_ids():
    """assets and asset_ids must stay aligned — a receiver correlating them by index
    must not fall off the end."""
    alert = {
        'id': 'a1',
        'asset_ids': [str(i) for i in range(150)],
        'assets': [{'id': str(i), 'label': f'host-{i}'} for i in range(150)],
    }
    p = build_alert_payload(alert, {})
    assert len(p['assets']) == 100
    assert [a['id'] for a in p['assets']] == p['asset_ids']


def test_build_alert_payload_raises_on_malformed_asset_entry():
    """assets must stay the same length and same order as asset_ids — built the same
    unfiltered way — so a receiver can pair each id with its label by index. A
    non-dict entry must fail loudly rather than being silently skipped: skipping it
    would shorten `assets` by one and shift every later id/label pairing out of
    alignment with the still-unfiltered asset_ids (e.g. 'c'/'d' would land on
    asset_ids' 'b'/'c' positions here)."""
    alert = {
        'id': 'a1',
        'asset_ids': ['a', 'b', 'c', 'd'],
        'assets': [{'id': 'a', 'label': 'host-a'}, 'not-a-dict',
                   {'id': 'c', 'label': 'host-c'}, {'id': 'd', 'label': 'host-d'}],
    }
    with pytest.raises(TypeError):
        build_alert_payload(alert, {})
