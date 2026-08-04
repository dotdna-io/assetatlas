# SPDX-License-Identifier: AGPL-3.0-or-later
"""asset detail status exposes grace-period state (#795)."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from src.modules.assets.services.asset_service import AssetService


def _service():
    return AssetService(repository=MagicMock(), alerts_repository=None)


def test_present_asset_not_in_grace():
    status = _service()._calculate_asset_status({"id": "x", "missing_since": None})
    assert status.in_grace is False
    assert status.days_in_grace is None


def test_carried_asset_in_grace_with_day_count():
    missing = datetime.now(timezone.utc) - timedelta(days=12, hours=1)
    status = _service()._calculate_asset_status({"id": "x", "missing_since": missing})
    assert status.in_grace is True
    assert status.days_in_grace == 12


def test_naive_missing_since_treated_as_utc():
    missing = (datetime.now(timezone.utc) - timedelta(days=3, hours=1)).replace(
        tzinfo=None
    )
    status = _service()._calculate_asset_status({"id": "x", "missing_since": missing})
    assert status.in_grace is True
    assert status.days_in_grace == 3
