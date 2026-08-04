# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single construction point for MetricsForwardingService (#922 P2b), reused by the
API factory. Mirrors alert_delivery/wiring.py."""

from src.modules.metrics_forwarding.repository import MetricsForwardingRepository
from src.modules.metrics_forwarding.service import MetricsForwardingService


def build_metrics_forwarding_service(
    db, config_dir=None, reload_url=None
) -> MetricsForwardingService:
    return MetricsForwardingService(
        MetricsForwardingRepository(db), config_dir=config_dir, reload_url=reload_url
    )
