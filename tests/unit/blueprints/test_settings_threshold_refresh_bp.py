# SPDX-License-Identifier: AGPL-3.0-or-later
"""Task 1b — saving a confidence threshold refreshes the dashboard MVs.

The dashboard materialized views bucket confidence high/medium/low through
the ``confidence_threshold_{high,medium}()`` SQL functions. When the operator
changes either threshold the views must be refreshed so the new boundaries
take effect immediately. These tests exercise the PUT
``/api/settings/<category>/<key>`` handler and assert that
``dashboard_repository.refresh_materialized_views()`` is (and isn't) called.

They live under ``tests/unit/blueprints/`` to inherit the canonical
JWT/auth fixtures (``blueprint_app`` with mocked services + ``authed_headers``).
"""

from src.api.blueprints.settings.settings_routes import _is_confidence_threshold


# ---------------------------------------------------------------------------
# Pure helper — the refresh decision
# ---------------------------------------------------------------------------

class TestIsConfidenceThreshold:
    """``_is_confidence_threshold`` gates the dashboard MV refresh."""

    def test_true_for_medium(self):
        assert _is_confidence_threshold(
            "asset_generation", "confidence_threshold_medium"
        ) is True

    def test_true_for_high(self):
        assert _is_confidence_threshold(
            "asset_generation", "confidence_threshold_high"
        ) is True

    def test_false_for_other_asset_generation_key(self):
        assert _is_confidence_threshold(
            "asset_generation", "pipeline_schedule_enabled"
        ) is False

    def test_false_for_threshold_key_in_other_category(self):
        # The key alone is not enough — category must be asset_generation.
        assert _is_confidence_threshold(
            "features", "confidence_threshold_high"
        ) is False

    def test_false_for_unrelated_setting(self):
        assert _is_confidence_threshold("ui", "theme") is False


# ---------------------------------------------------------------------------
# PUT /api/settings/<category>/<key> — refresh side effect
# ---------------------------------------------------------------------------

class TestThresholdSaveRefreshesViews:
    """Saving a confidence threshold triggers a dashboard MV refresh."""

    def test_saving_high_threshold_refreshes_views(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.settings_manager.set.return_value = (True, None)

        # high > medium so cross-field validation passes (mirrors
        # test_high_accepts_gt_medium in test_settings_bp.py).
        def _get(cat, key, default=None):
            if cat == "asset_generation" and key == "confidence_threshold_medium":
                return 0.45
            if cat == "asset_generation" and key == "confidence_threshold_high":
                return 0.80
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/asset_generation/confidence_threshold_high",
            json={"value": 0.80, "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_repository.refresh_materialized_views.assert_called_once()

    def test_saving_medium_threshold_refreshes_views(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get(cat, key, default=None):
            if cat == "asset_generation" and key == "confidence_threshold_high":
                return 0.70
            if cat == "asset_generation" and key == "confidence_threshold_medium":
                return 0.50
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/asset_generation/confidence_threshold_medium",
            json={"value": 0.50, "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_repository.refresh_materialized_views.assert_called_once()

    def test_saving_unrelated_setting_does_not_refresh(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get(cat, key, default=None):
            if cat == "ui" and key == "theme":
                return "dark"
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/ui/theme",
            json={"value": "dark", "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_repository.refresh_materialized_views.assert_not_called()

    def test_refresh_failure_does_not_break_save(
        self, client, authed_headers, blueprint_app
    ):
        """A failed MV refresh is best-effort: the save still returns 200."""
        blueprint_app.settings_manager.set.return_value = (True, None)
        blueprint_app.dashboard_repository.refresh_materialized_views.side_effect = (
            RuntimeError("boom")
        )

        def _get(cat, key, default=None):
            if cat == "asset_generation" and key == "confidence_threshold_medium":
                return 0.45
            if cat == "asset_generation" and key == "confidence_threshold_high":
                return 0.80
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/asset_generation/confidence_threshold_high",
            json={"value": 0.80, "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_repository.refresh_materialized_views.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/settings/<category>/<key>/reset — refresh side effect
# ---------------------------------------------------------------------------

class TestThresholdResetRefreshesViews:
    """Resetting a confidence threshold to default also refreshes the MVs —
    a reset changes the bucket boundaries just like a save does."""

    def test_resetting_threshold_refreshes_views(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.settings_manager.reset.return_value = (True, None)
        blueprint_app.settings_manager.get = lambda cat, key, default=None: 0.45

        resp = client.post(
            "/api/settings/asset_generation/confidence_threshold_medium/reset",
            json={"reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_repository.refresh_materialized_views.assert_called_once()

    def test_resetting_unrelated_setting_does_not_refresh(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.settings_manager.reset.return_value = (True, None)
        blueprint_app.settings_manager.get = lambda cat, key, default=None: "dark"

        resp = client.post(
            "/api/settings/ui/theme/reset",
            json={"reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_repository.refresh_materialized_views.assert_not_called()
