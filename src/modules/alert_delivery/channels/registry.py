# SPDX-License-Identifier: AGPL-3.0-or-later
"""Channel type registry: maps a channel `type` string to its AlertChannel impl."""
from typing import Dict, List

from src.modules.alert_delivery.channels.base import AlertChannel


class UnknownChannelType(KeyError):
    """Raised when a channel type has no registered implementation."""


_REGISTRY: Dict[str, AlertChannel] = {}


def register(channel_cls) -> None:
    """Register a channel class by its `type`. Idempotent (last registration wins)."""
    instance = channel_cls()
    _REGISTRY[instance.type] = instance


def get(channel_type: str) -> AlertChannel:
    try:
        return _REGISTRY[channel_type]
    except KeyError:
        raise UnknownChannelType(channel_type)


def available_types() -> List[str]:
    return sorted(_REGISTRY)


# Register built-ins. Import for side effect; #918-921 add their own register() calls.
from src.modules.alert_delivery.channels.log_channel import LogChannel  # noqa: E402

register(LogChannel)

from src.modules.alert_delivery.channels.webhook_channel import WebhookChannel  # noqa: E402

register(WebhookChannel)

from src.modules.alert_delivery.channels.email_channel import EmailChannel  # noqa: E402

register(EmailChannel)

from src.modules.alert_delivery.channels.syslog_channel import SyslogChannel  # noqa: E402

register(SyslogChannel)
