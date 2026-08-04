# SPDX-License-Identifier: AGPL-3.0-or-later
"""Webhook alert delivery channel (#918).

POST a versioned JSON envelope of a fired alert to a configurable URL, with an optional
HMAC signature and encrypted secrets. Built on #917: a single POST that CLASSIFIES its
outcome — the drain worker owns retry/backoff, so this does NOT wrap a retrying Session.
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

import requests

from src.modules.alert_delivery.channels.base import AlertChannel, DeliveryResult
from src.modules.integrations.shared.encryption import is_encrypted

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MIN_TIMEOUT = 1
MAX_TIMEOUT = 120


class WebhookChannel(AlertChannel):
    type = 'webhook'
    sensitive_fields = ('signing_key', 'headers')

    @staticmethod
    def _headers_dict(config: Dict[str, Any]) -> Dict[str, str]:
        """Custom headers are a dict on the create/update path and a JSON string on the
        send path (decrypted stored value). Normalize both to a dict."""
        raw = (config or {}).get('headers')
        if raw is None or raw == '':
            return {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raise ValueError('headers must be a JSON object of string values')
        if not isinstance(raw, dict):
            raise ValueError('headers must be a JSON object of string values')
        return raw

    def validate_config(self, config: dict) -> None:
        config = config or {}
        url = config.get('url')
        if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
            raise ValueError('url is required and must start with http:// or https://')
        for k, v in self._headers_dict(config).items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError('headers must be a flat object of string keys and values')
        signing_key = config.get('signing_key')
        if signing_key not in (None, '') and not isinstance(signing_key, str):
            raise ValueError('signing_key must be a string')
        timeout = config.get('timeout', DEFAULT_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, int) \
                or not (MIN_TIMEOUT <= timeout <= MAX_TIMEOUT):
            raise ValueError(
                f'timeout must be an integer between {MIN_TIMEOUT} and {MAX_TIMEOUT} seconds')

    def send(self, config: dict, payload: dict) -> DeliveryResult:
        config = config or {}
        url = config.get('url')
        if not isinstance(url, str) or not url:
            # send() must never raise for a delivery failure (ABC contract). A
            # validated channel always has a url; a mis-stored/config-less one fails
            # permanently rather than crashing the drain or send-test path.
            return DeliveryResult(ok=False, error='webhook channel has no url configured',
                                  retriable=False)
        # Secrets reach send() already decrypted (get_channel(decrypt_secrets=True)). If
        # decryption failed — e.g. an encryption-key mismatch after a restore/rotation —
        # the repository leaves the ciphertext in place. Signing with it would ship a
        # silently-unverifiable signature, and json.loads on ciphertext headers would
        # raise (breaking the ABC "send never raises" contract). Fail permanently with a
        # clear reason instead.
        for field in self.sensitive_fields:
            v = config.get(field)
            if isinstance(v, str) and is_encrypted(v):
                return DeliveryResult(
                    ok=False, retriable=False,
                    error='webhook channel secrets could not be decrypted '
                          '(encryption key mismatch?)')
        timeout = config.get('timeout', DEFAULT_TIMEOUT)
        try:
            envelope = {
                'event': 'alert.fired',
                'version': '1',
                'sent_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'alert': payload,
            }
            body = json.dumps(envelope, separators=(',', ':')).encode('utf-8')
            headers = dict(self._headers_dict(config))
            headers['Content-Type'] = 'application/json'
            headers['X-AssetAtlas-Event'] = 'alert.fired'
            signing_key = config.get('signing_key')
            if signing_key:
                sig = hmac.new(signing_key.encode('utf-8'), body, hashlib.sha256).hexdigest()
                headers['X-AssetAtlas-Signature'] = f'sha256={sig}'
        except (ValueError, TypeError) as e:
            # Corrupt stored headers JSON or a non-serializable payload: a permanent
            # config error, surfaced as a failed delivery rather than a raised exception.
            return DeliveryResult(ok=False, retriable=False,
                                  error=f'invalid webhook config: {e}')
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            return DeliveryResult(ok=False, error=str(e), retriable=True)
        code = resp.status_code
        if 200 <= code < 300:
            return DeliveryResult(ok=True, response_code=code)
        retriable = code == 429 or 500 <= code < 600
        return DeliveryResult(ok=False, response_code=code, retriable=retriable,
                              error=f'HTTP {code}')
