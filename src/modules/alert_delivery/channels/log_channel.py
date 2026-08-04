# SPDX-License-Identifier: AGPL-3.0-or-later
"""Built-in dummy/test channel. Records the payload to the log and reports success.

`config.simulate` drives failure paths so the retry/backoff + audit machinery is
testable with no network:
  - 'fail'           -> transient failure (retriable)
  - 'fail_permanent' -> permanent failure (not retriable)
"""
import logging

from src.modules.alert_delivery.channels.base import AlertChannel, DeliveryResult

logger = logging.getLogger(__name__)


class LogChannel(AlertChannel):
    type = 'log'
    sensitive_fields = ()
    user_creatable = False

    def send(self, config: dict, payload: dict) -> DeliveryResult:
        simulate = (config or {}).get('simulate')
        if simulate == 'fail':
            return DeliveryResult(ok=False, error='simulated transient failure', retriable=True)
        if simulate == 'fail_permanent':
            return DeliveryResult(ok=False, error='simulated permanent failure', retriable=False)
        logger.info("[log-channel] delivered alert %s (%s)",
                    payload.get('alert_id'), payload.get('title'))
        return DeliveryResult(ok=True)
