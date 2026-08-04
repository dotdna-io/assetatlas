# SPDX-License-Identifier: AGPL-3.0-or-later
import socket
import threading

import pytest

from src.modules.alert_delivery.channels import registry
from src.modules.alert_delivery.channels.base import DeliveryResult
from src.modules.alert_delivery.channels.syslog_channel import (
    REASON_MAX_LEN,
    SyslogChannel,
    _build_record,
)


def test_registered_and_no_secret_fields():
    ch = registry.get('syslog')
    assert isinstance(ch, SyslogChannel)
    assert ch.sensitive_fields == ()
    assert ch.user_creatable is True


@pytest.mark.parametrize('config', [
    {},
    {'host': ''},
    {'host': 123},
    {'host': 'x', 'port': 0},
    {'host': 'x', 'port': 70000},
    {'host': 'x', 'port': True},
    {'host': 'x', 'protocol': 'ftp'},
    {'host': 'x', 'facility': 'nope'},
])
def test_validate_config_rejects_bad(config):
    with pytest.raises(ValueError):
        SyslogChannel().validate_config(config)


@pytest.mark.parametrize('config', [
    {'host': 'collector'},
    {'host': 'collector', 'port': 6514, 'protocol': 'tcp', 'facility': 'local3'},
    {'host': 'collector', 'protocol': 'udp', 'facility': 'user'},
])
def test_validate_config_accepts_valid(config):
    SyslogChannel().validate_config(config)


def _sample_payload():
    return {
        'alert_id': 'a1', 'rule_slug': 'disk-low', 'severity': 'high',
        'type': 'compliance', 'title': 'Disk low', 'asset_count': 3,
        'data_sources': ['nmap', 'wazuh'], 'pipeline_run_id': 'run-9',
        'created_at': '2026-07-07T00:00:00Z',
    }


def test_build_record_pri_and_header():
    rec = _build_record(_sample_payload(), 'local0').decode('utf-8')
    # local0(16)*8 + high(3) = 131
    assert rec.startswith('<131>1 2026-07-07T00:00:00Z ')
    parts = rec.split(' ')
    # <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID ...
    assert parts[0] == '<131>1'
    assert parts[3] == 'assetatlas'   # APP-NAME
    assert parts[4] == '-'            # PROCID
    assert parts[5] == 'compliance'   # MSGID from alert type


@pytest.mark.parametrize('severity,code', [
    ('critical', 130), ('high', 131), ('medium', 132), ('low', 133),
    ('bogus', 133), (None, 133),
])
def test_build_record_severity_maps_to_pri(severity, code):
    rec = _build_record({'title': 't', 'severity': severity}, 'local0').decode('utf-8')
    assert rec.startswith(f'<{code}>1 ')


def test_build_record_structured_data_and_escaping():
    payload = {'alert_id': 'a"1', 'rule_slug': 'r]x', 'severity': 'low',
               'title': 'hi', 'data_sources': ['s1', 's2'],
               'pipeline_run_id': 'p\\q'}
    rec = _build_record(payload, 'local0').decode('utf-8')
    assert '[assetatlas@32473 ' in rec
    assert 'alert_id="a\\"1"' in rec       # escaped quote
    assert 'rule_slug="r\\]x"' in rec       # escaped ]
    assert 'data_sources="s1, s2"' in rec
    assert 'pipeline_run_id="p\\\\q"' in rec   # backslash escaped to two backslashes


def test_build_record_nil_sd_when_no_attributes():
    rec = _build_record({'title': 'only title'}, 'local0').decode('utf-8')
    # severity/attrs missing -> nil SD '-', then ' ' + BOM + MSG
    assert rec.endswith(' - \ufeffonly title')


def test_build_record_msg_has_bom():
    rec = _build_record({'title': 'Hello'}, 'local0').decode('utf-8')
    assert rec.endswith('\ufeffHello')


def test_build_record_msgid_fallback_and_sanitize():
    assert _build_record({'title': 't'}, 'local0').split(b' ')[5] == b'alert'
    msgid = _build_record({'title': 't', 'type': 'a b' * 40}, 'local0').split(b' ')[5]
    assert len(msgid) <= 32 and b' ' not in msgid


def test_send_udp_delivers_to_collector():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(('127.0.0.1', 0))
    rx.settimeout(5)
    port = rx.getsockname()[1]
    try:
        res = SyslogChannel().send(
            {'host': '127.0.0.1', 'port': port, 'protocol': 'udp'},
            {'title': 'ping', 'severity': 'high', 'type': 'compliance'})
        assert res.ok is True
        data, _ = rx.recvfrom(65535)
    finally:
        rx.close()
    text = data.decode('utf-8')
    assert text.startswith('<131>1 ')
    assert text.endswith('\ufeffping')


def test_send_tcp_delivers_octet_counted_frame():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = {}

    def accept():
        conn, _ = srv.accept()
        with conn:
            conn.settimeout(5)
            received['data'] = conn.recv(65535)

    t = threading.Thread(target=accept)
    t.start()
    try:
        res = SyslogChannel().send(
            {'host': '127.0.0.1', 'port': port, 'protocol': 'tcp'},
            {'title': 'ping', 'severity': 'low'})
    finally:
        t.join(5)
        srv.close()
    assert res.ok is True
    length, _, msg = received['data'].partition(b' ')
    assert int(length) == len(msg)            # octet count == record length
    assert msg.endswith('\ufeffping'.encode('utf-8'))


def test_send_tcp_connection_refused_is_retriable():
    # Reserve then release a port so the connect is refused deterministically.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    res = SyslogChannel().send(
        {'host': '127.0.0.1', 'port': port, 'protocol': 'tcp'}, {'title': 'x'})
    assert res.ok is False and res.retriable is True and res.error


def test_send_classifies_socket_error_as_retriable(monkeypatch):
    def boom(*a, **k):
        raise OSError('connection refused')
    monkeypatch.setattr(SyslogChannel, '_send_udp', staticmethod(boom))
    res = SyslogChannel().send({'host': 'x', 'protocol': 'udp'}, {'title': 't'})
    assert res.ok is False and res.retriable is True and res.error == 'connection refused'


def test_send_without_host_fails_permanently():
    res = SyslogChannel().send({}, {'title': 'x'})
    assert isinstance(res, DeliveryResult)
    assert res.ok is False and res.retriable is False
    assert 'host' in res.error


def test_build_record_never_raises_on_odd_payload():
    # non-string severity/title/type must not raise (formatting str()-ifies).
    rec = _build_record({'severity': 2, 'title': None, 'type': 123}, 'local0')
    assert isinstance(rec, bytes)


@pytest.mark.parametrize('protocol', ['udp', 'tcp'])
def test_send_never_raises_on_host_with_embedded_nul(protocol):
    # A NUL in the host reaches the socket layer as a NON-OSError (UDP sendto -> TypeError;
    # some platforms' getaddrinfo -> ValueError). host.strip() does not reject it, so send()
    # must catch it and return a failed result rather than let it escape (ABC contract;
    # send_test calls send() unwrapped, so a raise = HTTP 500). Whether it lands as
    # permanent (TypeError/ValueError) or transient (gaierror is an OSError, platform
    # dependent) is not the point — never raising is.
    res = SyslogChannel().send(
        {'host': 'collector\x00evil', 'port': 514, 'protocol': protocol},
        {'title': 't', 'severity': 'low'})
    assert isinstance(res, DeliveryResult)
    assert res.ok is False


def test_send_never_raises_on_non_finite_port():
    # float('inf') survives _clean_nan (only NaN is stripped) and round-trips through
    # JSONB as 'Infinity'. int(float('inf')) raises OverflowError (not TypeError/ValueError);
    # the coercion must swallow it and fall back to the default port, never raising.
    res = SyslogChannel().send(
        {'host': '127.0.0.1', 'port': float('inf'), 'protocol': 'udp'},
        {'title': 't', 'severity': 'low'})
    assert isinstance(res, DeliveryResult)


def test_build_record_timestamp_falls_back_when_created_at_has_space():
    # A space-bearing created_at (e.g. Postgres textual '2026-07-07 00:00:00+00') would
    # split the RFC5424 TIMESTAMP token and shift every following header field. _timestamp
    # must reject it and stamp send time, keeping HOSTNAME/APP-NAME/MSGID aligned.
    rec = _build_record(
        {'title': 't', 'created_at': '2026-07-07 00:00:00+00'}, 'local0').decode('utf-8')
    header = rec.split('[', 1)[0]   # drop SD, where created_at legitimately appears quoted
    parts = header.split(' ')
    # <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID — a space-bearing TIMESTAMP
    # would shift every field right; assert the fixed tokens stayed in position.
    assert parts[3] == 'assetatlas'   # APP-NAME
    assert parts[4] == '-'            # PROCID
    assert parts[5] == 'alert'        # MSGID fallback
    assert parts[1] != '2026-07-07'   # TIMESTAMP is the send-time fallback, not the split date


def test_send_never_raises_on_unencodable_title():
    # A lone surrogate in the title makes _build_record's UTF-8 encode raise
    # (UnicodeEncodeError <: ValueError); send() must catch it and fail permanently
    # rather than propagate (ABC never-raise contract; send_test calls send unwrapped).
    res = SyslogChannel().send(
        {'host': '127.0.0.1', 'port': 1, 'protocol': 'udp'},
        {'title': '\udc80 bad'})
    assert isinstance(res, DeliveryResult)
    assert res.ok is False and res.retriable is False


@pytest.mark.parametrize('bad_config', [
    {'host': '127.0.0.1', 'protocol': 123},
    {'host': '127.0.0.1', 'port': '514'},
    {'host': '127.0.0.1', 'port': 70000},
    {'host': '127.0.0.1', 'facility': ['local0']},
])
def test_send_never_raises_on_mis_stored_config(bad_config):
    # Configs that bypassed validate_config (non-str protocol, non-int/out-of-range
    # port, unhashable facility) must be coerced, not raised — send() never raises.
    res = SyslogChannel().send(bad_config, {'title': 't', 'severity': 'low'})
    assert isinstance(res, DeliveryResult)


def test_record_carries_the_reason_in_structured_data():
    """The reason is the one line that says why this fired. Syslog is machine-facing,
    so it belongs in the SD where it is greppable — MSG stays the title, the card's
    headline."""
    record = _build_record({
        'alert_id': 'a1', 'severity': 'high', 'type': 'security',
        'title': 'Assets missing endpoint protection',
        'reason': '12 assets — above the threshold of 5',
    }, 'local0').decode('utf-8')
    assert 'reason="12 assets — above the threshold of 5"' in record
    assert record.endswith('Assets missing endpoint protection')


def test_record_escapes_reserved_characters_in_reason():
    """RFC5424 SD-PARAM values reserve \\, " and ]; an unescaped one corrupts
    SD-PARAM framing for the whole record (SD-PARAMs aren't length-delimited).
    Escaping runs after truncation (see the cap test below) — if that order were
    reversed, escaping a value already cut at REASON_MAX_LEN could split a
    two-char escape sequence and break framing. Keeping the reason well under
    REASON_MAX_LEN here means truncation never engages, so this exercises
    escaping on its own."""
    reason = 'x' * 100 + ' "quoted" ] \\ tail'
    record = _build_record({
        'alert_id': 'a1', 'severity': 'high', 'type': 'security', 'title': 't',
        'reason': reason,
    }, 'local0').decode('utf-8')
    # A plain `.split('"')` extraction would mis-parse at the first escaped
    # quote, so assert the exact escaped SD-PARAM value appears instead.
    assert 'reason="' + 'x' * 100 + ' \\"quoted\\" \\] \\\\ tail"' in record


def test_record_truncates_a_long_reason_to_the_exact_cap():
    """A UDP datagram is bounded (~1472 bytes on a typical MTU) and RFC5424 only
    guarantees receivers handle 480, so an untruncated reason risks silent
    collector-side truncation of the whole record. Assert against REASON_MAX_LEN
    itself rather than a hardcoded bound, so a regression in how the cap is
    enforced fails the test instead of passing underneath a stale number."""
    reason = 'x' * (REASON_MAX_LEN + 100) + ' "quoted" ] \\ tail'
    record = _build_record({
        'alert_id': 'a1', 'severity': 'high', 'type': 'security', 'title': 't',
        'reason': reason,
    }, 'local0').decode('utf-8')
    truncated = record.split('reason="')[1].split('"')[0]
    assert len(truncated) == REASON_MAX_LEN
    assert 'tail' not in truncated


def test_record_uses_the_alerts_detect_time_not_send_time():
    """created_at is now real (it was null for every delivery), so the RFC5424
    TIMESTAMP becomes the event's time — correct per the RFC, and stable across
    retries."""
    record = _build_record({
        'alert_id': 'a1', 'severity': 'high', 'type': 'security', 'title': 't',
        'created_at': '2026-07-16T14:32:05+00:00',
    }, 'local0').decode('utf-8')
    assert '2026-07-16T14:32:05+00:00' in record
