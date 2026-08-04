# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the settings blueprint (admin auth required).

Endpoints tested:
- GET  /api/settings               — get all application settings
- GET  /api/settings/schema        — get settings schema definition
- GET  /api/settings/<category>    — get settings by category
- GET  /api/settings/<cat>/<key>   — get single setting
- PUT  /api/settings/<cat>/<key>   — update single setting
- POST /api/settings/<cat>/<key>/reset — reset setting to default
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Auth gate — all endpoints require admin
# ---------------------------------------------------------------------------

class TestSettingsAuth:
    """All settings endpoints require admin JWT."""

    @pytest.mark.parametrize("method,path", [
        ("GET",  "/api/settings"),
        ("GET",  "/api/settings/schema"),
        ("GET",  "/api/settings/features"),
        ("GET",  "/api/settings/features/dark_mode"),
        ("PUT",  "/api/settings/features/dark_mode"),
        ("POST", "/api/settings/features/dark_mode/reset"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("GET",  "/api/settings"),
        ("GET",  "/api/settings/schema"),
        ("GET",  "/api/settings/features"),
        ("GET",  "/api/settings/features/dark_mode"),
        ("PUT",  "/api/settings/features/dark_mode"),
        ("POST", "/api/settings/features/dark_mode/reset"),
    ])
    def test_user_role_returns_403(self, client, authed_headers, method, path):
        resp = getattr(client, method.lower())(path, headers=authed_headers("user"))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------

class TestGetAllSettings:
    """GET /api/settings — returns all settings grouped by category."""

    def test_returns_all_settings(self, client, authed_headers, blueprint_app):
        mock_settings = {
            "features": {"dark_mode": True},
            "notifications": {"email_enabled": False},
        }
        blueprint_app.settings_manager.get_all.return_value = mock_settings

        resp = client.get("/api/settings", headers=authed_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["settings"] == mock_settings
        assert sorted(body["data"]["categories"]) == ["features", "notifications"]

    def test_service_called(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.get_all.return_value = {}

        client.get("/api/settings", headers=authed_headers("admin"))
        blueprint_app.settings_manager.get_all.assert_called_once()


# ---------------------------------------------------------------------------
# GET /api/settings/schema
# ---------------------------------------------------------------------------

class TestGetSettingsSchema:
    """GET /api/settings/schema — returns settings schema definition."""

    def test_returns_schema(self, client, authed_headers, blueprint_app):
        mock_schema = {
            "features": {
                "dark_mode": {"type": "boolean", "default": False},
            },
        }
        blueprint_app.settings_manager.get_schema.return_value = mock_schema

        resp = client.get("/api/settings/schema", headers=authed_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["schema"] == mock_schema


# ---------------------------------------------------------------------------
# GET /api/settings/<category>
# ---------------------------------------------------------------------------

class TestGetCategorySettings:
    """GET /api/settings/<category> — returns settings for one category."""

    def test_returns_category(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.get_all.return_value = {
            "features": {"dark_mode": True, "beta_enabled": False},
        }

        resp = client.get("/api/settings/features", headers=authed_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["category"] == "features"
        assert body["data"]["settings"]["dark_mode"] is True

    def test_unknown_category_returns_404(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.get_all.return_value = {
            "features": {"dark_mode": True},
        }

        resp = client.get(
            "/api/settings/nonexistent", headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/settings/<category>/<key>
# ---------------------------------------------------------------------------

class TestGetSingleSetting:
    """GET /api/settings/<category>/<key> — returns one setting value."""

    def test_returns_single_setting(self, client, authed_headers, blueprint_app):
        def _get_setting(cat, key, default=None):
            if cat == "features" and key == "dark_mode":
                return True
            return default

        blueprint_app.settings_manager.get = _get_setting

        resp = client.get(
            "/api/settings/features/dark_mode", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["category"] == "features"
        assert body["data"]["key"] == "dark_mode"
        assert body["data"]["value"] is True

    def test_not_found_returns_404(self, client, authed_headers, blueprint_app):
        def _get_setting(cat, key, default=None):
            if cat == "features" and key == "nonexistent":
                raise KeyError("not found")
            return default

        blueprint_app.settings_manager.get = _get_setting

        resp = client.get(
            "/api/settings/features/nonexistent", headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/settings/<category>/<key>
# ---------------------------------------------------------------------------

class TestUpdateSetting:
    """PUT /api/settings/<category>/<key> — update a single setting."""

    def test_update_success(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get_setting(cat, key, default=None):
            if cat == "ui" and key == "theme":
                return "dark"
            return default

        blueprint_app.settings_manager.get = _get_setting

        resp = client.put(
            "/api/settings/ui/theme",
            json={"value": "dark", "reason": "User preference"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["message"] == "Setting updated successfully"
        assert body["data"]["value"] == "dark"
        assert body["data"]["reason"] == "User preference"

    def test_missing_value_returns_400(self, client, authed_headers):
        resp = client.put(
            "/api/settings/ui/theme",
            json={"reason": "no value field"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_not_found_returns_404(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.set.return_value = (
            False, "Setting not found in schema",
        )

        resp = client.put(
            "/api/settings/ui/nonexistent",
            json={"value": "x"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_validation_error_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.set.return_value = (
            False, "Value must be a boolean",
        )

        resp = client.put(
            "/api/settings/features/dark_mode",
            json={"value": "not-a-bool"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_default_reason_used(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get_setting(cat, key, default=None):
            if cat == "features" and key == "dark_mode":
                return True
            return default

        blueprint_app.settings_manager.get = _get_setting

        resp = client.put(
            "/api/settings/features/dark_mode",
            json={"value": True},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["reason"] == "Setting updated via API"

    def test_set_called_with_user_context(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get_setting(cat, key, default=None):
            if cat == "ui" and key == "font_size":
                return 42
            return default

        blueprint_app.settings_manager.get = _get_setting

        with patch(
            "src.api.blueprints.settings.settings_routes.get_current_user_context"
        ) as mock_ctx:
            mock_ctx.return_value = ("user-123", "admin_user")

            resp = client.put(
                "/api/settings/ui/font_size",
                json={"value": 42},
                headers=authed_headers("admin"),
            )
            assert resp.status_code == 200

            call_kwargs = blueprint_app.settings_manager.set.call_args.kwargs
            assert call_kwargs["user_id"] == "user-123"
            assert call_kwargs["username"] == "admin_user"
            assert call_kwargs["category"] == "ui"
            assert call_kwargs["key"] == "font_size"
            assert call_kwargs["value"] == 42


# ---------------------------------------------------------------------------
# POST /api/settings/<category>/<key>/reset
# ---------------------------------------------------------------------------

class TestResetSetting:
    """POST /api/settings/<category>/<key>/reset — reset to default."""

    def test_reset_success(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.reset.return_value = (True, None)

        def _get_setting(cat, key, default=None):
            if cat == "features" and key == "dark_mode":
                return False
            return default

        blueprint_app.settings_manager.get = _get_setting

        resp = client.post(
            "/api/settings/features/dark_mode/reset",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["message"] == "Setting reset to default successfully"
        assert body["data"]["value"] is False

    def test_reset_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.reset.return_value = (
            False, "Setting not defined in schema",
        )

        resp = client.post(
            "/api/settings/features/nonexistent/reset",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_reset_with_custom_reason(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.reset.return_value = (True, None)

        def _get_setting(cat, key, default=None):
            if cat == "ui" and key == "theme":
                return "default_val"
            return default

        blueprint_app.settings_manager.get = _get_setting

        resp = client.post(
            "/api/settings/ui/theme/reset",
            json={"reason": "Reverting bad change"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200

        call_kwargs = blueprint_app.settings_manager.reset.call_args.kwargs
        assert call_kwargs["reason"] == "Reverting bad change"

    def test_reset_no_body_uses_default_reason(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.reset.return_value = (True, None)

        def _get_setting(cat, key, default=None):
            if cat == "ui" and key == "theme":
                return "val"
            return default

        blueprint_app.settings_manager.get = _get_setting

        resp = client.post(
            "/api/settings/ui/theme/reset",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200

        call_kwargs = blueprint_app.settings_manager.reset.call_args.kwargs
        assert call_kwargs["reason"] == "Setting reset to default via API"

    def test_reset_validation_error(self, client, authed_headers, blueprint_app):
        blueprint_app.settings_manager.reset.return_value = (
            False, "Cannot reset locked setting",
        )

        resp = client.post(
            "/api/settings/features/locked_setting/reset",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Cross-field validation for confidence thresholds
# ---------------------------------------------------------------------------

class TestConfidenceThresholdValidation:
    """Cross-field validation for confidence threshold settings."""

    def test_medium_rejects_gte_high(self, client, authed_headers, blueprint_app):
        """Setting medium >= current high should fail."""
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get(cat, key, default=None):
            if cat == "asset_generation" and key == "confidence_threshold_high":
                return 0.70
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/asset_generation/confidence_threshold_medium",
            json={"value": 0.75, "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_medium_rejects_equal_high(self, client, authed_headers, blueprint_app):
        """Setting medium == current high should also fail."""
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get(cat, key, default=None):
            if cat == "asset_generation" and key == "confidence_threshold_high":
                return 0.70
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/asset_generation/confidence_threshold_medium",
            json={"value": 0.70, "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_high_rejects_lte_medium(self, client, authed_headers, blueprint_app):
        """Setting high <= current medium should fail."""
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get(cat, key, default=None):
            if cat == "asset_generation" and key == "confidence_threshold_medium":
                return 0.45
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/asset_generation/confidence_threshold_high",
            json={"value": 0.40, "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_high_rejects_equal_medium(self, client, authed_headers, blueprint_app):
        """Setting high == current medium should also fail."""
        blueprint_app.settings_manager.set.return_value = (True, None)

        def _get(cat, key, default=None):
            if cat == "asset_generation" and key == "confidence_threshold_medium":
                return 0.45
            return default

        blueprint_app.settings_manager.get = _get

        resp = client.put(
            "/api/settings/asset_generation/confidence_threshold_high",
            json={"value": 0.45, "reason": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_medium_accepts_lt_high(self, client, authed_headers, blueprint_app):
        """Setting medium < current high should succeed."""
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

    def test_high_accepts_gt_medium(self, client, authed_headers, blueprint_app):
        """Setting high > current medium should succeed."""
        blueprint_app.settings_manager.set.return_value = (True, None)

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
