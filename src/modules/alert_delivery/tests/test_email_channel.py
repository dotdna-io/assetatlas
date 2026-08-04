# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from src.modules.alert_delivery.channels import registry, email_channel
from src.modules.alert_delivery.channels.base import DeliveryResult
from src.modules.alert_delivery.channels.email_channel import EmailChannel
from src.auth.services.email_service import EMAIL_NOT_CONFIGURED


class _FakeEmailService:
    def __init__(self, result=(True, None, False), base_url='https://assetatlas.example.com'):
        self.result = result
        self.base_url = base_url
        self.calls = []

    def send_alert_email(self, recipients, subject, body):
        self.calls.append({'recipients': recipients, 'subject': subject, 'body': body})
        return self.result

    def get_public_base_url(self):
        return self.base_url


@pytest.fixture(autouse=True)
def _reset_email_service():
    # set_email_service mutates a module global; save/restore so tests don't leak.
    prev = email_channel.get_email_service()
    yield
    email_channel.set_email_service(prev)


def test_registered_and_no_secret_fields():
    ch = registry.get('email')
    assert isinstance(ch, EmailChannel)
    assert ch.sensitive_fields == ()
    assert ch.user_creatable is True


@pytest.mark.parametrize('config', [
    {}, {'recipients': []}, {'recipients': ['not-an-email']}, {'recipients': [123]},
])
def test_validate_config_rejects_bad_recipients(config):
    with pytest.raises(ValueError):
        EmailChannel().validate_config(config)


def test_validate_config_accepts_valid_recipients():
    EmailChannel().validate_config({'recipients': ['a@x.com', 'b@y.com']})


def test_send_success_maps_to_delivery_result():
    fake = _FakeEmailService((True, None, False))
    email_channel.set_email_service(fake)
    payload = {'alert_id': 'a1', 'severity': 'high', 'title': 'Disk low',
               'rule_name': 'Disk rule', 'type': 'operational',
               'description': 'root at 95%', 'asset_count': 3,
               'data_sources': ['nmap', 'wazuh']}
    res = EmailChannel().send({'recipients': ['a@x.com', 'b@y.com']}, payload)
    assert isinstance(res, DeliveryResult) and res.ok is True
    call = fake.calls[0]
    assert call['recipients'] == ['a@x.com', 'b@y.com']
    assert call['subject'] == '[AssetAtlas][HIGH] Disk low'
    # The card's meta line: severity · type label · subject-aware count. The title is
    # the subject, not repeated in the body, and rule_name is gone — the card shows
    # neither (it renders alert.title, which for a rule alert IS the rule name).
    assert 'HIGH · Operational · 3 assets' in call['body']
    assert 'root at 95%' in call['body']


def test_send_transient_failure_is_retriable():
    email_channel.set_email_service(_FakeEmailService((False, 'boom', True)))
    res = EmailChannel().send({'recipients': ['a@x.com']}, {'title': 'x', 'severity': 'low'})
    assert res.ok is False and res.retriable is True and res.error == 'boom'


def test_send_unconfigured_returns_clear_permanent_error():
    email_channel.set_email_service(_FakeEmailService((False, EMAIL_NOT_CONFIGURED, False)))
    res = EmailChannel().send({'recipients': ['a@x.com']}, {'title': 'x'})
    assert res.ok is False and res.retriable is False
    assert 'SMTP is not configured' in res.error


def test_send_without_recipients_fails_permanently_without_calling_service():
    fake = _FakeEmailService()
    email_channel.set_email_service(fake)
    res = EmailChannel().send({'recipients': []}, {'title': 'x'})
    assert res.ok is False and res.retriable is False
    assert fake.calls == []


def test_send_without_service_fails_permanently():
    email_channel.set_email_service(None)
    res = EmailChannel().send({'recipients': ['a@x.com']}, {'title': 'x'})
    assert res.ok is False and res.retriable is False


def test_send_never_raises_on_service_none_and_empty_payload():
    email_channel.set_email_service(None)
    res = EmailChannel().send({}, {})
    assert isinstance(res, DeliveryResult) and res.ok is False


def test_send_never_raises_on_non_string_severity():
    email_channel.set_email_service(_FakeEmailService((True, None, False)))
    res = EmailChannel().send({'recipients': ['a@x.com']}, {'severity': 2, 'title': 'x'})
    assert res.ok is True


def _send_and_body(payload, base_url='https://assetatlas.example.com'):
    svc = _FakeEmailService(base_url=base_url)
    email_channel.set_email_service(svc)
    EmailChannel().send({'recipients': ['ops@example.com']}, payload)
    return svc.calls[0]['body']


_CARD_PAYLOAD = {
    'alert_id': 'alert-1', 'rule_name': 'Missing endpoint protection',
    'type': 'security', 'severity': 'high',
    'title': 'Assets missing endpoint protection',
    'description': 'Some assets have no endpoint-protection agent reporting.',
    'reason': '12 assets — above the threshold of 5',
    'asset_count': 12, 'subject_type': 'asset',
    'assets_by_category': {'Server': 7, 'Laptop': 5},
    'data_sources': ['SentinelOne', 'Tanium'],
    'assets': [{'id': f'a{i}', 'label': f'host-{i:02d}'} for i in range(12)],
    'evidence': {'observed': 12, 'typical': 3},
    'created_at': '2026-07-16T14:32:05+00:00',
    'subjects': None,
}


def test_body_follows_the_card_reading_order():
    """The email must say what the card says, in the order the card says it:
    meta -> description -> reason -> evidence -> assets -> footer."""
    body = _send_and_body(_CARD_PAYLOAD)
    order = [
        'HIGH · Security · 12 assets',
        'Some assets have no endpoint-protection agent reporting.',
        'Why: 12 assets — above the threshold of 5',
        'Observed 12 vs the usual 3',
        'By category',
        'Affected assets (12)',
        'host-00',
        'Data sources: SentinelOne, Tanium',
        'Detected: 2026-07-16 14:32 UTC',
        'View in AssetAtlas',
        'https://assetatlas.example.com/alerts',
    ]
    positions = [body.find(s) for s in order]
    assert all(p >= 0 for p in positions), [s for s, p in zip(order, positions) if p < 0]
    assert positions == sorted(positions), body


def test_body_omits_reason_when_it_duplicates_the_description():
    """Same rule as the card (AlertsPage.tsx:267) — never print the same sentence
    twice under two labels."""
    body = _send_and_body({**_CARD_PAYLOAD, 'reason': _CARD_PAYLOAD['description']})
    assert 'Why:' not in body
    assert body.count(_CARD_PAYLOAD['description']) == 1


def test_body_uses_the_subject_type_noun():
    """A non-asset alert must never count in 'assets' (AlertsPage.tsx:248-249), and its
    entities render as the card's subjects pane (AlertSubjects.tsx PANE_TITLE)."""
    body = _send_and_body({
        **_CARD_PAYLOAD, 'subject_type': 'integration', 'asset_count': 3,
        'description': 'A data source stopped reporting.',
        'reason': '3 data sources — see below',
        'assets': [], 'assets_by_category': {}, 'evidence': None,
        'subjects': [{'id': 'i1', 'label': 'SentinelOne', 'sublabel': 'last synced 3d ago'}],
    })
    assert '3 data sources' in body
    # The count noun specifically — not a blanket 'assets' scan, which any English
    # description could trip.
    assert '3 assets' not in body
    assert 'Affected assets' not in body
    assert 'Data Sources' in body
    assert 'SentinelOne — last synced 3d ago' in body


def test_body_omits_the_count_for_summary_only_alerts():
    """pipeline_run/metric/system alerts carry asset_count=0. The card hides the count
    entirely; the old body printed a bare 'Affected assets: 0'."""
    body = _send_and_body({
        **_CARD_PAYLOAD, 'subject_type': 'system', 'asset_count': 0, 'assets': [],
        'assets_by_category': {}, 'evidence': None,
        'reason': 'The latest pipeline run matched this rule.',
    })
    assert 'Affected assets' not in body
    assert '0 assets' not in body
    assert 'HIGH · Security' in body
    assert 'Why: The latest pipeline run matched this rule.' in body


def test_body_caps_the_asset_list_and_counts_the_remainder_off_asset_count():
    """The card shows 12 then '+N more' (AlertsPage.tsx:205-206). asset_ids/assets are
    capped at 100 in the payload, so the remainder must come off asset_count — the true
    total — not off the truncated list."""
    body = _send_and_body({
        **_CARD_PAYLOAD, 'asset_count': 5000,
        'assets': [{'id': f'a{i}', 'label': f'host-{i:02d}'} for i in range(100)],
    })
    assert 'host-11' in body
    assert 'host-12' not in body
    assert '+4988 more' in body


def test_body_renders_the_labelled_sample_for_metric_alerts():
    """Metric-subject anomaly findings carry no asset list — their entities live in
    evidence['sample'] (AlertEvidence.tsx:44-61)."""
    body = _send_and_body({
        **_CARD_PAYLOAD, 'subject_type': 'metric', 'asset_count': 0, 'assets': [],
        'assets_by_category': {},
        'evidence': {'observed': 40, 'typical': 4,
                     'sample': [{'id': 'a1', 'label': 'web-prod-01'},
                                {'id': 'a2', 'label': 'db-prod-02'}]},
    })
    assert 'Sample of affected assets (2 of 40)' in body
    assert 'web-prod-01' in body
    assert 'Affected assets (' not in body


def test_body_omits_the_link_block_when_no_base_url_is_configured():
    """Air-gapped installs may never set security.public_base_url. A bare '/alerts'
    or a dangling header would be worse than no link."""
    body = _send_and_body(_CARD_PAYLOAD, base_url='')
    assert 'View in AssetAtlas' not in body
    assert '/alerts' not in body
    assert 'Some assets have no endpoint-protection agent reporting.' in body


def test_body_drops_the_alert_id():
    """The card never shows it and UUIDs are banned from human copy (#979). It stays
    the webhook's idempotency key, where it belongs."""
    body = _send_and_body(_CARD_PAYLOAD)
    assert 'alert-1' not in body
    assert 'Alert ID' not in body


def test_body_shows_the_type_label_not_the_raw_slug():
    """'Data quality', not data_quality — mirrors AlertCard.tsx TYPE_LABELS."""
    body = _send_and_body({**_CARD_PAYLOAD, 'type': 'data_quality'})
    assert 'Data quality' in body
    assert 'data_quality' not in body


def test_body_falls_back_to_a_humanised_label_for_an_unknown_type():
    """An unmapped type must never leak its underscore slug into the body."""
    body = _send_and_body({**_CARD_PAYLOAD, 'type': 'some_new_type'})
    assert 'Some new type' in body
    assert 'some_new_type' not in body


def test_send_never_raises_on_a_malformed_payload():
    """ABC contract — send_test calls send() unwrapped, so a raise is an HTTP 500."""
    svc = _FakeEmailService()
    email_channel.set_email_service(svc)
    result = EmailChannel().send(
        {'recipients': ['ops@example.com']},
        {'severity': 12, 'asset_count': 'many', 'assets': ['not-a-dict'],
         'data_sources': 'SentinelOne', 'evidence': 'not-a-dict',
         'created_at': 'not-a-date', 'subject_type': None})
    assert isinstance(result, DeliveryResult)


@pytest.mark.parametrize('bad_payload', [
    # assets as a dict: assets[:_ASSET_LIST_CAP] -> KeyError(slice(...)) (dict slicing).
    {'severity': 'high', 'subject_type': 'asset', 'assets': {'a': 1}, 'asset_count': 5},
    # assets as an int: assets[:_ASSET_LIST_CAP] -> TypeError (not subscriptable).
    {'severity': 'high', 'subject_type': 'asset', 'assets': 42, 'asset_count': 5},
    # type as a list: _TYPE_LABELS.get(alert_type) -> TypeError (unhashable).
    {'severity': 'high', 'type': ['a', 'b']},
    # subject_type as a list: _SUBJECT_NOUNS.get(...) -> TypeError (unhashable).
    {'severity': 'high', 'subject_type': ['a'], 'asset_count': 3},
    # subjects as an int: `for s in subjects` -> TypeError (not iterable).
    {'severity': 'high', 'subject_type': 'integration', 'assets': [], 'evidence': {},
     'subjects': 5},
], ids=['assets-dict', 'assets-int', 'type-unhashable-list', 'subject_type-unhashable-list',
        'subjects-not-iterable'])
def test_send_never_raises_on_a_malformed_body_shape(bad_payload):
    """_format_body crashes on each of these five shapes today (assets/subjects as the
    wrong container type, type/subject_type as an unhashable dict key) — send() must
    turn the raise into a permanent DeliveryResult failure, never let it escape (ABC
    contract; send_test() calls send() unwrapped, so a raise is an HTTP 500)."""
    email_channel.set_email_service(_FakeEmailService())
    result = EmailChannel().send({'recipients': ['ops@example.com']}, bad_payload)
    assert isinstance(result, DeliveryResult)
    assert result.ok is False
    assert result.retriable is False


def test_body_renders_the_narrative_when_it_differs_from_the_reason():
    """AlertEvidence.tsx:23 — the narrative adds context beyond the reason and must
    still render when it says something the reason doesn't. Without this test, the
    render path of the `narrative != reason` check is never exercised."""
    narrative = 'This pattern matches the last 3 incidents seen on this host.'
    body = _send_and_body({**_CARD_PAYLOAD, 'evidence': {
        'narrative': narrative, 'observed': 12, 'typical': 3}})
    assert narrative in body


def test_body_suppresses_the_narrative_when_it_duplicates_the_reason():
    """Same rule as the card (AlertEvidence.tsx:23) — the narrative is dropped when it
    repeats the reason. Printing the same sentence twice under two labels is exactly
    what #979's copy work removed; must appear exactly once, not twice."""
    reason = _CARD_PAYLOAD['reason']
    body = _send_and_body({**_CARD_PAYLOAD, 'evidence': {
        'narrative': reason, 'observed': 12, 'typical': 3}})
    assert f"Why: {reason}" in body
    assert body.count(reason) == 1
