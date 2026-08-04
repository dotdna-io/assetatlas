# SPDX-License-Identifier: AGPL-3.0-or-later
"""Syslog (RFC5424) alert delivery channel (#920).

Emits a fired alert as one RFC5424 record to a configurable collector over UDP
or TCP (RFC 6587 octet-counting framing). No TLS this iteration. The channel is
fully self-contained — host/port/protocol/facility all live in config — so
unlike the email channel it needs no injected service and no app context.

Built on #917: a single send that CLASSIFIES its outcome; the drain worker owns
retry/backoff, so a transient socket error is retriable and a missing host is
permanent.
"""
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.modules.alert_delivery.channels.base import AlertChannel, DeliveryResult

logger = logging.getLogger(__name__)

DEFAULT_PORT = 514
TCP_TIMEOUT = 10  # seconds, connect + send
APP_NAME = 'assetatlas'
ENTERPRISE_ID = '32473'  # IANA documentation PEN (RFC 5612); swap if registered
SD_ID = f'assetatlas@{ENTERPRISE_ID}'
BOM = '\ufeff'

# RFC5424 facility keyword -> numeric code (the ones admins actually pick).
FACILITIES: Dict[str, int] = {
    'kern': 0, 'user': 1, 'daemon': 3, 'security': 4, 'syslog': 5,
    'local0': 16, 'local1': 17, 'local2': 18, 'local3': 19,
    'local4': 20, 'local5': 21, 'local6': 22, 'local7': 23,
}
DEFAULT_FACILITY = 'local0'

# Alert severity -> RFC5424 numeric severity.
SEVERITY_MAP = {'critical': 2, 'high': 3, 'medium': 4, 'low': 5}
DEFAULT_SYSLOG_SEVERITY = 5  # Notice, for an unknown/missing severity

# A UDP datagram is bounded (~1472 bytes on a typical MTU) and RFC5424 only guarantees
# receivers handle 480 bytes. An untruncated reason plus the rest of the SD could push
# the record past that and get the whole thing silently truncated by the collector.
REASON_MAX_LEN = 256

# Scalar attributes copied into the SD-ELEMENT, in a stable order.
_SD_KEYS = (
    'alert_id', 'rule_slug', 'severity', 'type', 'reason', 'asset_count',
    'pipeline_run_id', 'created_at',
)


def _nil(value: Optional[str]) -> str:
    """RFC5424 nil value '-' for an empty field."""
    return value if isinstance(value, str) and value else '-'


# Computed once at import — the FQDN is effectively constant per process, and
# socket.getfqdn() can trigger a blocking reverse-DNS lookup on every call.
_HOSTNAME = _nil(socket.getfqdn())[:255]


def _sanitize_msgid(value: Any) -> str:
    """MSGID: printable ASCII (33..126), no spaces, <=32 chars; 'alert' if empty."""
    s = ''.join(c for c in str(value or '') if 33 <= ord(c) <= 126)
    return s[:32] or 'alert'


def _sd_escape(value: str) -> str:
    r"""Escape the three chars RFC5424 reserves inside an SD-PARAM value: \\ " ]"""
    return value.replace('\\', '\\\\').replace('"', '\\"').replace(']', '\\]')


def _timestamp(payload: Dict[str, Any]) -> str:
    created = payload.get('created_at')
    # An RFC5424 TIMESTAMP is a single whitespace-free header token. A space-bearing
    # value (e.g. the Postgres textual form '2026-07-07 00:00:00+00') would split the
    # field and shift HOSTNAME/APP-NAME/MSGID, corrupting every following header token,
    # so fall back to send time rather than emit an unparseable record.
    if isinstance(created, str) and created and not any(c.isspace() for c in created):
        return created
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _structured_data(payload: Dict[str, Any]) -> str:
    params: List[str] = []
    for key in _SD_KEYS:
        value = payload.get(key)
        if value is None or value == '':
            continue
        text = str(value)
        if key == 'reason':
            text = text[:REASON_MAX_LEN]
        params.append(f'{key}="{_sd_escape(text)}"')
    data_sources = payload.get('data_sources') or []
    if isinstance(data_sources, str):
        data_sources = [data_sources]
    if data_sources:
        joined = ', '.join(str(s) for s in data_sources)
        params.append(f'data_sources="{_sd_escape(joined)}"')
    if not params:
        return '-'
    return f'[{SD_ID} ' + ' '.join(params) + ']'


def _build_record(payload: Dict[str, Any], facility: str) -> bytes:
    severity_num = SEVERITY_MAP.get(
        str(payload.get('severity') or '').lower(), DEFAULT_SYSLOG_SEVERITY)
    pri = FACILITIES[facility] * 8 + severity_num
    timestamp = _timestamp(payload)
    hostname = _HOSTNAME
    msgid = _sanitize_msgid(payload.get('type'))
    sd = _structured_data(payload)
    msg = str(payload.get('title') or 'Alert')
    header = f'<{pri}>1 {timestamp} {hostname} {APP_NAME} - {msgid} {sd}'
    return (header + ' ' + BOM + msg).encode('utf-8')


class SyslogChannel(AlertChannel):
    type = 'syslog'
    sensitive_fields = ()
    user_creatable = True

    def validate_config(self, config: dict) -> None:
        config = config or {}
        host = config.get('host')
        if not isinstance(host, str) or not host.strip():
            raise ValueError('host is required and must be a non-empty string')
        port = config.get('port', DEFAULT_PORT)
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError('port must be an integer between 1 and 65535')
        protocol = config.get('protocol', 'udp')
        if protocol not in ('udp', 'tcp'):
            raise ValueError("protocol must be 'udp' or 'tcp'")
        facility = config.get('facility', DEFAULT_FACILITY)
        if facility not in FACILITIES:
            raise ValueError('facility must be one of: ' + ', '.join(sorted(FACILITIES)))

    def send(self, config: dict, payload: dict) -> DeliveryResult:
        config = config or {}
        payload = payload or {}
        host = config.get('host')
        if not isinstance(host, str) or not host.strip():
            # ABC never-raise contract: a mis-stored/config-less channel fails
            # permanently rather than crashing the drain or the send-test path.
            return DeliveryResult(ok=False, retriable=False,
                                  error='syslog channel has no host configured')
        host = host.strip()
        # Defensive coercion mirrors the host guard above: a config that bypassed
        # validate_config (direct DB insert / version skew) must still never raise
        # (ABC contract; send_test() calls send() unwrapped). A non-int port,
        # non-string protocol, or unhashable facility would otherwise throw.
        try:
            port = int(config.get('port') or DEFAULT_PORT)
        except (TypeError, ValueError, OverflowError):
            # OverflowError: a non-finite float port (e.g. float('inf'), which _clean_nan
            # does NOT strip) reaches int() and would otherwise escape send().
            port = DEFAULT_PORT
        if not (1 <= port <= 65535):
            port = DEFAULT_PORT
        protocol = str(config.get('protocol') or 'udp').lower()
        facility = config.get('facility') or DEFAULT_FACILITY
        if not isinstance(facility, str) or facility not in FACILITIES:
            facility = DEFAULT_FACILITY
        try:
            record = _build_record(payload, facility)
        except (ValueError, TypeError) as e:
            # A malformed payload (e.g. a lone surrogate in the title) is a permanent
            # data error — surface it without a socket attempt and without raising
            # (ABC contract; send_test() calls send() unwrapped, so a raise = HTTP 500).
            return DeliveryResult(ok=False, retriable=False,
                                  error=f'invalid alert payload: {e}')
        try:
            if protocol == 'tcp':
                self._send_tcp(host, port, record)
            else:
                self._send_udp(host, port, record)
        except OSError as e:
            # Refused / timeout / DNS failure / unreachable — all transient from the
            # drain's view; it owns backoff + max-attempts.
            return DeliveryResult(ok=False, retriable=True, error=str(e))
        except (ValueError, TypeError) as e:
            # A malformed host reaches the socket layer as a NON-OSError: sendto()
            # raises TypeError on an embedded NUL, and getaddrinfo raises
            # ValueError/UnicodeError on a NUL / un-encodable IDNA label. host.strip()
            # in validate_config and above does not reject these, so a stored host
            # like 'a\x00b' would otherwise crash. Permanent config error, never raised
            # (ABC contract; send_test() calls send() unwrapped, so a raise = HTTP 500).
            return DeliveryResult(ok=False, retriable=False,
                                  error=f'invalid syslog host: {e}')
        return DeliveryResult(ok=True)

    @staticmethod
    def _send_udp(host: str, port: int, record: bytes) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(record, (host, port))
        finally:
            sock.close()

    @staticmethod
    def _send_tcp(host: str, port: int, record: bytes) -> None:
        frame = f'{len(record)} '.encode('ascii') + record  # RFC 6587 octet-counting
        sock = socket.create_connection((host, port), timeout=TCP_TIMEOUT)
        try:
            sock.settimeout(TCP_TIMEOUT)
            sock.sendall(frame)
        finally:
            sock.close()
