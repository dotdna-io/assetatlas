# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration test: PostureEvaluationService.evaluate_all() populates
the assets.posture_rules JSONB column for every asset row.

Calls the service directly (no HTTP layer), using the real database.

`live_app` is supplied by tests/integration/conftest.py (session-scoped).
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


def test_evaluate_all_populates_posture_rules(live_app):
    """evaluate_all() writes all 5 category keys to assets.posture_rules."""
    db = live_app.db_adapter
    service = live_app.posture_evaluation_service

    asset_id = str(uuid.uuid4())
    db.execute_query(
        "INSERT INTO assets (id, category) VALUES (%(id)s::uuid, 'server')",
        {"id": asset_id},
        fetch=False,
    )
    try:
        result = service.evaluate_all()
        assert result["updated_count"] >= 1

        rows = db.execute_query(
            "SELECT posture_rules FROM assets WHERE id = %(id)s::uuid",
            {"id": asset_id},
        )
        assert len(rows) == 1
        keys = set(rows[0]["posture_rules"].keys())
        assert keys == {
            "secured",
            "vulnerability_scanning",
            "domain_joined",
            "device_management",
            "monitored",
        }
        for v in rows[0]["posture_rules"].values():
            assert isinstance(v, dict)
            assert v.get("state") in ("true", "false", "n/a")
            assert "output" in v
    finally:
        db.execute_query(
            "DELETE FROM assets WHERE id = %(id)s::uuid",
            {"id": asset_id},
            fetch=False,
        )
