# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock

from src.modules.alert_delivery.service import AlertDeliveryService
from src.modules.alert_delivery.wiring import build_alert_delivery_service


def test_build_returns_service_with_repository():
    svc = build_alert_delivery_service(MagicMock(), settings_manager=MagicMock())
    assert isinstance(svc, AlertDeliveryService)
    assert svc.repo is not None


def test_build_without_settings_manager_auto_builds_from_conn_manager():
    # s20's DB-direct build (no Flask app context) passes no settings_manager. Without
    # auto-building one here, ALERT_DELIVERY_ENABLED would be invisible on the enqueue
    # path (see wiring.py) and a disabled switch would silently keep queuing rows.
    svc = build_alert_delivery_service(MagicMock())
    assert svc.settings is not None
