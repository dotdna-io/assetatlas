# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single construction point for AlertDeliveryService, reused by the API factory,
the scheduler worker, and s20's DB-direct build so all three share one wiring."""
from src.modules.alert_delivery.repository import AlertDeliveryRepository
from src.modules.alert_delivery.service import AlertDeliveryService


def build_alert_delivery_service(db, settings_manager=None) -> AlertDeliveryService:
    # When no settings_manager is supplied (s20's DB-direct, no-app-context build),
    # construct one from the adapter's connection manager so ALERT_DELIVERY_ENABLED is
    # honored on the ENQUEUE path too. Otherwise the pipeline keeps queuing deliveries
    # while delivery is disabled, and re-enabling flushes a stale backlog. The drain
    # already gates on the switch; this closes the enqueue side (defense in depth).
    if settings_manager is None:
        conn_manager = getattr(db, 'conn_manager', None)
        if conn_manager is not None:
            from src.api.modules.settings.settings_manager import SettingsManager
            settings_manager = SettingsManager(conn_manager)
    return AlertDeliveryService(AlertDeliveryRepository(db), settings_manager=settings_manager)
