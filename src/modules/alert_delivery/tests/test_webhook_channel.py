# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import hmac
import json

import pytest
import requests

from src.modules.alert_delivery.channels import registry, webhook_channel
from src.modules.alert_delivery.channels.base import DeliveryResult
from src.modules.alert_delivery.channels.webhook_channel import WebhookChannel


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def _capture(monkeypatch):
    box = {'calls': [], 'resp': _FakeResp(200), 'exc': None}

    def fake_post(url, data=None, headers=None, timeout=None):
        box['calls'].append({'url': url, 'data': data, 'headers': headers, 'timeout': timeout})
        if box['exc'] is not None:
            raise box['exc']
        return box['resp']

    monkeypatch.setattr(webhook_channel.requests, 'post', fake_post)
    return box


def test_registered_and_sensitive_fields():
    ch = registry.get('webhook')
    assert isinstance(ch, WebhookChannel)
    assert ch.sensitive_fields == ('signing_key', 'headers')


def test_send_posts_versioned_envelope(monkeypatch):
    box = _capture(monkeypatch)
    payload = {'alert_id': 'a1', 'title': 'Disk low', 'severity': 'high'}
    res = WebhookChannel().send({'url': 'https://hook.example/x', 'timeout': 15}, payload)
    assert res.ok is True and res.response_code == 200
    call = box['calls'][0]
    assert call['url'] == 'https://hook.example/x' and call['timeout'] == 15
    body = json.loads(call['data'])
    assert body['event'] == 'alert.fired' and body['version'] == '1'
    assert body['sent_at'].endswith('Z') and body['alert'] == payload
    assert call['headers']['Content-Type'] == 'application/json'
    assert call['headers']['X-AssetAtlas-Event'] == 'alert.fired'
    assert 'X-AssetAtlas-Signature' not in call['headers']


def test_send_signs_body_when_key_present(monkeypatch):
    box = _capture(monkeypatch)
    WebhookChannel().send({'url': 'https://x', 'signing_key': 'shhh'}, {'alert_id': 'a1'})
    call = box['calls'][0]
    expected = 'sha256=' + hmac.new(b'shhh', call['data'], hashlib.sha256).hexdigest()
    assert call['headers']['X-AssetAtlas-Signature'] == expected


def test_send_includes_custom_headers_from_json_string(monkeypatch):
    box = _capture(monkeypatch)
    WebhookChannel().send({'url': 'https://x', 'headers': json.dumps({'X-Api-Key': 'k1'})},
                          {'alert_id': 'a1'})
    assert box['calls'][0]['headers']['X-Api-Key'] == 'k1'


@pytest.mark.parametrize('code,ok,retriable', [
    (200, True, None), (201, True, None), (204, True, None),
    (400, False, False), (401, False, False), (404, False, False),
    (429, False, True), (500, False, True), (503, False, True),
])
def test_send_classifies_status_codes(monkeypatch, code, ok, retriable):
    box = _capture(monkeypatch)
    box['resp'] = _FakeResp(code)
    res = WebhookChannel().send({'url': 'https://x'}, {'alert_id': 'a1'})
    assert res.ok is ok and res.response_code == code
    if not ok:
        assert res.retriable is retriable


def test_send_timeout_is_retriable(monkeypatch):
    box = _capture(monkeypatch)
    box['exc'] = requests.Timeout('too slow')
    res = WebhookChannel().send({'url': 'https://x'}, {'alert_id': 'a1'})
    assert res.ok is False and res.retriable is True and res.error


@pytest.mark.parametrize('config,msg', [
    ({}, 'url'), ({'url': 'ftp://x'}, 'url'),
    ({'url': 'https://x', 'timeout': 0}, 'timeout'),
    ({'url': 'https://x', 'timeout': 999}, 'timeout'),
    ({'url': 'https://x', 'headers': {'a': 1}}, 'headers'),
    ({'url': 'https://x', 'signing_key': ''}, None),
])
def test_validate_config(config, msg):
    ch = WebhookChannel()
    if msg is None:
        ch.validate_config(config)
    else:
        with pytest.raises(ValueError) as e:
            ch.validate_config(config)
        assert msg in str(e.value)


def test_send_never_raises_on_connection_error(monkeypatch):
    box = _capture(monkeypatch)
    box['exc'] = requests.ConnectionError('nope')
    res = WebhookChannel().send({'url': 'https://x'}, {})
    assert isinstance(res, DeliveryResult) and res.ok is False and res.retriable is True


def test_send_without_url_fails_permanently_without_posting(monkeypatch):
    # send() must not raise for a misconfigured channel (ABC contract): a config with
    # no url returns a permanent failure and never attempts a POST.
    box = _capture(monkeypatch)
    res = WebhookChannel().send({}, {'alert_id': 'a1'})
    assert res.ok is False and res.retriable is False
    assert box['calls'] == []


@pytest.mark.parametrize('field', ['signing_key', 'headers'])
def test_send_fails_permanently_when_secret_cannot_be_decrypted(monkeypatch, field):
    # If a secret reaches send() still encrypted (decrypt failed upstream on a key
    # mismatch), send() must NOT raise (json.loads on ciphertext headers would) and must
    # NOT sign with the ciphertext — it fails permanently and never POSTs. Guards the
    # send-test path (service.send_test calls send() unwrapped, so a raise = HTTP 500).
    box = _capture(monkeypatch)
    res = WebhookChannel().send({'url': 'https://x', field: 'ENC:gAAAAAB_not_decryptable'},
                                {'alert_id': 'a1'})
    assert res.ok is False and res.retriable is False
    assert 'decrypt' in res.error
    assert box['calls'] == []
