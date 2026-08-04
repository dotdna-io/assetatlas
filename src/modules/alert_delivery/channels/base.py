# SPDX-License-Identifier: AGPL-3.0-or-later
"""Channel abstraction for outbound alert delivery (#917).

One AlertChannel subclass per delivery type. #917 ships only LogChannel (the
built-in dummy/test channel); #918-921 register webhook/email/syslog/HEC.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class DeliveryResult:
    """Outcome of a single send attempt.

    retriable distinguishes a transient failure (5xx, timeout — worth a backoff
    retry) from a permanent one (bad config, 4xx — fail now, don't burn attempts).
    """
    ok: bool
    response_code: Optional[int] = None
    error: Optional[str] = None
    retriable: bool = True


class AlertChannel(ABC):
    type: str = ''
    sensitive_fields: Tuple[str, ...] = ()
    user_creatable: bool = True

    def validate_config(self, config: dict) -> None:
        """Raise ValueError if config is invalid for this channel type. Default: no-op."""

    @abstractmethod
    def send(self, config: dict, payload: dict) -> DeliveryResult:
        """Deliver one alert payload through this channel. Must not raise for a
        normal delivery failure — return DeliveryResult(ok=False) instead."""
