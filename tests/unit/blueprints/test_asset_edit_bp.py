# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the asset edit blueprint (auth required, writes admin-only).

Endpoints tested:
- PUT    /api/assets/<id>/classify       — reclassify asset (admin)
- GET    /api/assets/categories           — list valid categories
- GET    /api/assets/<id>/tags            — list manual tags
- POST   /api/assets/<id>/tags            — add tag (admin)
- DELETE /api/assets/<id>/tags/<tag_id>   — remove tag (admin)
- POST   /api/assets/tags-bulk            — bulk add one tag by filter (admin)
- GET    /api/assets/tags-bulk/candidates — manual tags on the filtered set (admin)
- POST   /api/assets/tags-bulk/remove     — bulk remove one tag by filter (admin)
- POST   /api/assets/merge                — execute merge (admin)
- POST   /api/assets/<id>/unmerge         — undo merge (admin)
- GET    /api/assets/<id>/merge-history   — merge history
- GET    /api/assets/<id>/merge-preview   — preview merge
- GET    /api/assets/<id>/category-override    — override state + predictions
- DELETE /api/assets/<id>/category-override    — restore predicted (admin)
"""

import json
from datetime import datetime, timezone

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

class TestAssetEditAuthRequired:
    """All asset-edit endpoints require a valid JWT."""

    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/assets/categories"),
        ("POST",   "/api/assets/categories/Server/subcategories"),
        ("PUT",    "/api/assets/abc/classify"),
        ("GET",    "/api/assets/abc/tags"),
        ("POST",   "/api/assets/abc/tags"),
        ("DELETE", "/api/assets/abc/tags/t1"),
        ("POST",   "/api/assets/merge"),
        ("POST",   "/api/assets/abc/unmerge"),
        ("GET",    "/api/assets/abc/merge-history"),
        ("GET",    "/api/assets/abc/merge-preview?target_id=xyz"),
        ("POST",   "/api/assets/classify-bulk"),
        ("POST",   "/api/assets/classify-bulk/clear"),
        ("POST",   "/api/assets/tags-bulk"),
        ("GET",    "/api/assets/tags-bulk/candidates"),
        ("POST",   "/api/assets/tags-bulk/remove"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401


class TestAssetEditRBAC:
    """Write endpoints require admin role."""

    @pytest.mark.parametrize("method,path", [
        ("PUT",    "/api/assets/abc/classify"),
        ("POST",   "/api/assets/categories/Server/subcategories"),
        ("POST",   "/api/assets/abc/tags"),
        ("DELETE", "/api/assets/abc/tags/t1"),
        ("POST",   "/api/assets/merge"),
        ("POST",   "/api/assets/abc/unmerge"),
        ("POST",   "/api/assets/classify-bulk"),
        ("POST",   "/api/assets/classify-bulk/clear"),
        ("POST",   "/api/assets/tags-bulk"),
        # The picker is admin-only too: it exists to serve an admin write, and
        # a non-admin who could read it would learn nothing they could act on.
        ("GET",    "/api/assets/tags-bulk/candidates"),
        ("POST",   "/api/assets/tags-bulk/remove"),
    ])
    def test_user_role_returns_403(self, client, authed_headers, method, path):
        resp = getattr(client, method.lower())(
            path, json={}, headers=authed_headers("user")
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/assets/<asset_id>/classify
# ---------------------------------------------------------------------------

class TestReclassifyAsset:
    """PUT /api/assets/<asset_id>/classify"""

    def test_reclassify_success(self, client, authed_headers, blueprint_app):
        blueprint_app.reclassify_service.reclassify_asset.return_value = {
            "asset_id": "abc-123",
            "category": "Server",
            "subcategory": "Virtual",
        }

        resp = client.put(
            "/api/assets/abc-123/classify",
            json={"category": "Server", "subcategory": "Virtual"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_reclassify_missing_fields_returns_400(self, client, authed_headers):
        resp = client.put(
            "/api/assets/abc-123/classify",
            json={"category": "Server"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_reclassify_empty_body_returns_400(self, client, authed_headers):
        resp = client.put(
            "/api/assets/abc-123/classify",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_reclassify_asset_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.reclassify_service.reclassify_asset.side_effect = LookupError(
            "Asset not found"
        )

        resp = client.put(
            "/api/assets/abc-123/classify",
            json={"category": "Server", "subcategory": "Virtual"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_reclassify_invalid_category(self, client, authed_headers, blueprint_app):
        blueprint_app.reclassify_service.reclassify_asset.side_effect = ValueError(
            "Invalid category"
        )

        resp = client.put(
            "/api/assets/abc-123/classify",
            json={"category": "BadCat", "subcategory": "BadSub"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/assets/categories
# ---------------------------------------------------------------------------

class TestGetCategories:
    """GET /api/assets/categories"""

    def test_categories_success(self, client, authed_headers, blueprint_app):
        blueprint_app.reclassify_service.get_categories.return_value = [
            {"category": "Server", "subcategories": ["Physical", "Virtual"]},
        ]

        resp = client.get("/api/assets/categories", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "categories" in body["data"]


# ---------------------------------------------------------------------------
# POST /api/assets/categories/<category>/subcategories
# ---------------------------------------------------------------------------

class TestAddCustomSubcategory:
    """POST /api/assets/categories/<category>/subcategories"""

    def test_add_success(self, client, authed_headers, blueprint_app):
        blueprint_app.reclassify_service.add_custom_subcategory.return_value = {
            "category": "server",
            "subcategory": "Edge Server",
            "created_by": "test-user-id",
        }

        resp = client.post(
            "/api/assets/categories/server/subcategories",
            json={"name": "Edge Server"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["subcategory"] == "Edge Server"
        blueprint_app.reclassify_service.add_custom_subcategory.assert_called_once_with(
            category="server",
            name="Edge Server",
            user="test-user-id",
        )

    def test_add_missing_name_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/assets/categories/server/subcategories",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_add_invalid_category_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.reclassify_service.add_custom_subcategory.side_effect = ValueError(
            "Invalid category 'BadCat'"
        )

        resp = client.post(
            "/api/assets/categories/BadCat/subcategories",
            json={"name": "Foo"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_add_duplicate_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.reclassify_service.add_custom_subcategory.side_effect = ValueError(
            "Subcategory 'edge' already exists for category 'server'"
        )

        resp = client.post(
            "/api/assets/categories/server/subcategories",
            json={"name": "edge"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tags CRUD
# ---------------------------------------------------------------------------

class TestTags:
    """Tag endpoints: GET / POST / DELETE on /api/assets/<id>/tags."""

    def test_get_tags_success(self, client, authed_headers, blueprint_app):
        blueprint_app.tag_service.get_tags.return_value = [
            {"id": "t1", "tag_name": "critical", "tag_color": "#ff0000"},
        ]

        resp = client.get("/api/assets/abc-123/tags", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "tags" in body["data"]

    def test_add_tag_success(self, client, authed_headers, blueprint_app):
        blueprint_app.tag_service.add_tag.return_value = {
            "id": "t2", "tag_name": "new-tag", "tag_color": "#64748b",
        }

        resp = client.post(
            "/api/assets/abc-123/tags",
            json={"tag_name": "new-tag"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_add_tag_with_color(self, client, authed_headers, blueprint_app):
        blueprint_app.tag_service.add_tag.return_value = {
            "id": "t3", "tag_name": "custom", "tag_color": "#abcdef",
        }

        resp = client.post(
            "/api/assets/abc-123/tags",
            json={"tag_name": "custom", "tag_color": "#abcdef"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.tag_service.add_tag.assert_called_once_with(
            asset_id="abc-123",
            tag_name="custom",
            tag_color="#abcdef",
            user="test-user-id",
        )

    def test_add_tag_missing_name_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/assets/abc-123/tags",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_add_tag_asset_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.tag_service.add_tag.side_effect = LookupError("Asset not found")

        resp = client.post(
            "/api/assets/abc-123/tags",
            json={"tag_name": "test"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_add_tag_duplicate_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.tag_service.add_tag.side_effect = ValueError("Tag already exists")

        resp = client.post(
            "/api/assets/abc-123/tags",
            json={"tag_name": "dup"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_remove_tag_success(self, client, authed_headers, blueprint_app):
        resp = client.delete(
            "/api/assets/abc-123/tags/t1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "removed" in body["data"]["message"].lower()

    def test_remove_tag_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.tag_service.remove_tag.side_effect = LookupError("Tag not found")

        resp = client.delete(
            "/api/assets/abc-123/tags/nonexistent",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Merge endpoints
# ---------------------------------------------------------------------------

class TestMerge:
    """POST /api/assets/merge and POST /api/assets/<id>/unmerge."""

    def test_merge_success(self, client, authed_headers, blueprint_app):
        blueprint_app.merge_service.execute_merge.return_value = {
            "merged_asset_id": "primary-id",
            "status": "merged",
        }

        resp = client.post(
            "/api/assets/merge",
            json={
                "primary_asset_id": "primary-id",
                "secondary_asset_id": "secondary-id",
                "merge_strategy": "keep_primary",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_merge_missing_fields_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/assets/merge",
            json={"primary_asset_id": "a"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_merge_asset_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.merge_service.execute_merge.side_effect = LookupError("Not found")

        resp = client.post(
            "/api/assets/merge",
            json={
                "primary_asset_id": "a",
                "secondary_asset_id": "b",
                "merge_strategy": "keep_primary",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_unmerge_success(self, client, authed_headers, blueprint_app):
        blueprint_app.merge_service.unmerge.return_value = {"status": "unmerged"}

        resp = client.post(
            "/api/assets/abc-123/unmerge",
            json={"secondary_asset_id": "sec-456"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200

    def test_unmerge_missing_secondary_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/assets/abc-123/unmerge",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Merge history & preview
# ---------------------------------------------------------------------------

class TestMergeHistoryPreview:
    """GET merge-history and merge-preview."""

    def test_merge_history_success(self, client, authed_headers, blueprint_app):
        blueprint_app.merge_service.get_merge_history.return_value = [
            {"merged_at": "2025-01-01", "secondary_id": "sec-1"},
        ]

        resp = client.get(
            "/api/assets/abc-123/merge-history",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "merge_history" in body["data"]

    def test_merge_preview_success(self, client, authed_headers, blueprint_app):
        blueprint_app.merge_service.preview_merge.return_value = {
            "conflicts": [], "merged_fields": {},
        }

        resp = client.get(
            "/api/assets/abc-123/merge-preview?target_id=xyz-456",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200

    def test_merge_preview_missing_target_returns_400(self, client, authed_headers):
        resp = client.get(
            "/api/assets/abc-123/merge-preview",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400

    def test_merge_preview_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.merge_service.preview_merge.side_effect = LookupError(
            "Asset not found"
        )

        resp = client.get(
            "/api/assets/abc-123/merge-preview?target_id=xyz-456",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Asset label override endpoints (#639)
# ---------------------------------------------------------------------------

class TestAssetLabelOverrideAuth:
    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/assets/abc/label-override"),
        ("PUT",    "/api/assets/abc/label-override"),
        ("DELETE", "/api/assets/abc/label-override"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("PUT",    "/api/assets/abc/label-override"),
        ("DELETE", "/api/assets/abc/label-override"),
    ])
    def test_user_role_returns_403(self, client, authed_headers, method, path):
        resp = getattr(client, method.lower())(
            path, json={"label": "X"}, headers=authed_headers("user")
        )
        assert resp.status_code == 403


class TestGetAssetLabelOverride:
    def test_returns_existing_override(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_label_override_service.get_override.return_value = {
            "asset_id": "abc-123",
            "label": "Lab Printer",
            "overridden_by": "alice",
            "updated_at": "2026-05-09T12:00:00+00:00",
        }
        resp = client.get(
            "/api/assets/abc-123/label-override",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["label"] == "Lab Printer"

    def test_returns_null_when_missing(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_label_override_service.get_override.return_value = None
        resp = client.get(
            "/api/assets/abc-123/label-override",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"] == {"label": None}


class TestSetAssetLabelOverride:
    def test_set_success(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_label_override_service.set_override.return_value = {
            "id": "abc-123",
            "label": "Lab Printer",
            "previous_label": None,
            "overridden_by": "admin@test",
            "updated_at": "2026-05-09T12:00:00+00:00",
        }
        resp = client.put(
            "/api/assets/abc-123/label-override",
            json={"label": "Lab Printer"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["label"] == "Lab Printer"

    def test_set_missing_label_returns_400(self, client, authed_headers):
        resp = client.put(
            "/api/assets/abc/label-override",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_set_whitespace_label_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_label_override_service.set_override.side_effect = ValueError(
            "label must be a non-empty string"
        )
        resp = client.put(
            "/api/assets/abc/label-override",
            json={"label": "   "},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_set_asset_not_found_returns_404(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_label_override_service.set_override.side_effect = LookupError(
            "Asset not found"
        )
        resp = client.put(
            "/api/assets/abc/label-override",
            json={"label": "X"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


class TestClearAssetLabelOverride:
    def test_clear_success(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_label_override_service.clear_override.return_value = {
            "id": "abc-123",
            "label": None,
            "previous_label": "Lab Printer",
            "overridden_by": "admin@test",
            "updated_at": "2026-05-09T12:00:00+00:00",
        }
        resp = client.delete(
            "/api/assets/abc-123/label-override",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["previous_label"] == "Lab Printer"

    def test_clear_asset_not_found_returns_404(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_label_override_service.clear_override.side_effect = LookupError(
            "Asset not found"
        )
        resp = client.delete(
            "/api/assets/abc/label-override",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Category override endpoints (#1155)
# ---------------------------------------------------------------------------

CATEGORY_OVERRIDE_ID = "11111111-2222-3333-4444-555555555555"
CATEGORY_OVERRIDE_URL = f"/api/assets/{CATEGORY_OVERRIDE_ID}/category-override"

# What ReclassifyService.get_override_state hands back: attribution off the
# durable category_overrides row, predictions off the assets row, timestamp
# still a datetime (the blueprint is what isoformats it).
OVERRIDE_STATE = {
    "asset_id": CATEGORY_OVERRIDE_ID,
    "is_overridden": True,
    "overridden_by": "jsmith",
    "overridden_at": datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
    "reason": "Mislabelled by the OUI fallback",
    "category_predicted": "server",
    "subcategory_predicted": "linux",
}

NO_OVERRIDE_STATE = {
    "asset_id": CATEGORY_OVERRIDE_ID,
    "is_overridden": False,
    "overridden_by": None,
    "overridden_at": None,
    "reason": None,
    "category_predicted": "server",
    "subcategory_predicted": "linux",
}


class TestCategoryOverrideAuth:
    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/assets/abc/category-override"),
        ("DELETE", "/api/assets/abc/category-override"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    def test_clear_requires_admin(self, client, authed_headers):
        resp = client.delete(
            "/api/assets/abc/category-override", headers=authed_headers("user")
        )
        assert resp.status_code == 403

    def test_the_read_is_open_to_any_authenticated_user(
        self, client, authed_headers, blueprint_app
    ):
        """The Explorer edit modal makes this read for everyone who can open it.

        Admin-gating a route the UI calls unconditionally produces a silent 403
        the user sees as an empty panel — it has already happened on this
        codebase (#1144's non-admin route). Asserted positively rather than by
        the absence of a 403 test, so nobody "tidies" @require_admin() onto it.
        """
        blueprint_app.reclassify_service.get_override_state.return_value = OVERRIDE_STATE

        resp = client.get(CATEGORY_OVERRIDE_URL, headers=authed_headers("user"))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["is_overridden"] is True


class TestGetCategoryOverride:
    """GET /api/assets/<id>/category-override"""

    def test_returns_the_state_for_an_overridden_asset(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.reclassify_service.get_override_state.return_value = OVERRIDE_STATE

        resp = client.get(CATEGORY_OVERRIDE_URL, headers=authed_headers("user"))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["is_overridden"] is True
        assert data["overridden_by"] == "jsmith"
        assert data["reason"] == "Mislabelled by the OUI fallback"
        assert data["category_predicted"] == "server"
        assert data["subcategory_predicted"] == "linux"

    def test_the_timestamp_is_serialised(self, client, authed_headers, blueprint_app):
        """A raw datetime would either 500 in the JSON encoder or reach the
        client in a shape it has to guess at — same treatment as
        get_label_override's updated_at."""
        blueprint_app.reclassify_service.get_override_state.return_value = OVERRIDE_STATE

        resp = client.get(CATEGORY_OVERRIDE_URL, headers=authed_headers("user"))

        assert resp.get_json()["data"]["overridden_at"] == "2026-07-14T09:30:00+00:00"

    def test_an_already_serialised_timestamp_is_passed_through(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.reclassify_service.get_override_state.return_value = {
            **OVERRIDE_STATE, "overridden_at": "2026-07-14T09:30:00+00:00",
        }

        resp = client.get(CATEGORY_OVERRIDE_URL, headers=authed_headers("user"))

        assert resp.get_json()["data"]["overridden_at"] == "2026-07-14T09:30:00+00:00"

    def test_no_override_returns_a_well_formed_body_not_a_404(
        self, client, authed_headers, blueprint_app
    ):
        """The tab renders this shape; a 404 would make "never reclassified" an
        error case the client has to special-case."""
        blueprint_app.reclassify_service.get_override_state.return_value = NO_OVERRIDE_STATE

        resp = client.get(CATEGORY_OVERRIDE_URL, headers=authed_headers("user"))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["is_overridden"] is False
        assert data["overridden_by"] is None
        assert data["overridden_at"] is None

    def test_a_malformed_asset_id_is_a_400(self, client, authed_headers):
        """The repository casts straight to ::uuid, and psycopg2's error is not a
        ValueError — without the guard a client typo answers 500 (#973)."""
        resp = client.get(
            "/api/assets/not-a-uuid/category-override", headers=authed_headers("user")
        )
        assert resp.status_code == 400


class TestClearCategoryOverride:
    """DELETE /api/assets/<id>/category-override"""

    def test_it_delegates_to_the_bulk_clear(self, client, authed_headers, blueprint_app):
        """Clearing correctly is three writes in one transaction — deactivate the
        override row, reset the live row to the predicted pair, write the
        chronicle event. `clear_overrides_bulk` already does all three, with the
        #1154 COALESCE restore and the #1270 order-independent guard. A
        second single-asset path would drift from it silently; this asserts the
        route never grows one.
        """
        blueprint_app.reclassify_service.clear_overrides_bulk.return_value = {"cleared": 1}

        resp = client.delete(CATEGORY_OVERRIDE_URL, headers=authed_headers("admin"))

        assert resp.status_code == 200
        blueprint_app.reclassify_service.clear_overrides_bulk.assert_called_once()
        kwargs = blueprint_app.reclassify_service.clear_overrides_bulk.call_args.kwargs
        assert kwargs["asset_ids"] == [CATEGORY_OVERRIDE_ID]

    def test_a_cleared_override_is_reported_as_cleared(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.reclassify_service.clear_overrides_bulk.return_value = {"cleared": 1}

        resp = client.delete(CATEGORY_OVERRIDE_URL, headers=authed_headers("admin"))

        assert resp.get_json()["data"]["cleared"] is True

    def test_clearing_an_unoverridden_asset_is_a_well_formed_no_op(
        self, client, authed_headers, blueprint_app
    ):
        """Idempotent, like clear_label_override. `cleared: false` is the whole
        difference between "there was nothing to undo" and "we undid it" — the
        service reports it as a count of 0 and the route must not flatten that
        into an unconditional success."""
        blueprint_app.reclassify_service.clear_overrides_bulk.return_value = {"cleared": 0}

        resp = client.delete(CATEGORY_OVERRIDE_URL, headers=authed_headers("admin"))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["cleared"] is False

    def test_a_non_canonical_uuid_is_canonicalised_before_delegating(
        self, client, authed_headers, blueprint_app
    ):
        """`clear_overrides_bulk` narrows by string-matching the ids it was given
        against `str(row['asset_id'])` from psycopg2 — always lowercase 8-4-4-4-12.
        Handing it the caller's spelling makes an uppercase id match nothing,
        clear nothing, and still answer 200: the exact silent-success shape
        #1270 was about.
        """
        blueprint_app.reclassify_service.clear_overrides_bulk.return_value = {"cleared": 1}

        resp = client.delete(
            f"/api/assets/{CATEGORY_OVERRIDE_ID.upper()}/category-override",
            headers=authed_headers("admin"),
        )

        assert resp.status_code == 200
        kwargs = blueprint_app.reclassify_service.clear_overrides_bulk.call_args.kwargs
        assert kwargs["asset_ids"] == [CATEGORY_OVERRIDE_ID]

    def test_a_malformed_asset_id_is_a_400(self, client, authed_headers):
        resp = client.delete(
            "/api/assets/not-a-uuid/category-override", headers=authed_headers("admin")
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/assets/classify-bulk
# ---------------------------------------------------------------------------

class TestBulkReclassify:
    """POST /api/assets/classify-bulk — resolve filter, enforce cap, delegate."""

    def test_happy_path(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1", "a2"]
        blueprint_app.reclassify_service.reclassify_bulk.return_value = {"updated": 2}

        resp = client.post(
            "/api/assets/classify-bulk",
            json={
                "filters": [], "search": "printer",
                "category": "Network", "subcategory": "Switch",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == {"updated": 2}
        blueprint_app.reclassify_service.reclassify_bulk.assert_called_once()

    def test_over_cap_writes_nothing(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["x"] * 1001

        resp = client.post(
            "/api/assets/classify-bulk",
            json={"filters": [], "category": "Network", "subcategory": "Switch"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        # Error envelope: {"error": "validation_error", "message": "...", "details": {"cap": 1000}}
        assert resp.get_json()["details"]["cap"] == 1000
        blueprint_app.reclassify_service.reclassify_bulk.assert_not_called()

    def test_empty_match_is_noop(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = []
        blueprint_app.reclassify_service.reclassify_bulk.return_value = {"updated": 0}

        resp = client.post(
            "/api/assets/classify-bulk",
            json={"filters": [], "category": "Network", "subcategory": "Switch"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == {"updated": 0}

    def test_missing_category_and_subcategory_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/assets/classify-bulk",
            json={"filters": []},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_malformed_filter_returns_400_without_calling_service(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.post(
            "/api/assets/classify-bulk",
            json={
                "filters": [{"bogus": "shape"}],
                "category": "Network",
                "subcategory": "Switch",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()

    def test_alert_scope_narrows_the_target_set_not_the_fleet(
        self, client, authed_headers, blueprint_app
    ):
        # The #1011 regression. Under an alert drilldown the only active scope is
        # the alert — filters=[] — and the count beside the button reads the
        # alert's size (list_assets honours alert_id). If _resolve_bulk_ids drops
        # alert_id, get_matching_asset_ids matches EVERY asset and the write lands
        # on the fleet while the UI claimed three. Assert the id reaches the
        # resolver, canonically spelled (uppercase in → lowercase through).
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.reclassify_service.reclassify_bulk.return_value = {"updated": 1}

        resp = client.post(
            "/api/assets/classify-bulk",
            json={
                "filters": [],
                "alert_id": "948E3F69-818B-4649-8F77-362048686475",
                "category": "Network", "subcategory": "Switch",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        kwargs = blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs
        assert kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"

    def test_malformed_alert_id_is_400_without_resolving(
        self, client, authed_headers, blueprint_app
    ):
        # ensure_uuid rejects a bad alert id in the handler, before the resolver
        # (and the ::uuid cast) ever sees it — a 400, never the 500 of #973/#999.
        resp = client.post(
            "/api/assets/classify-bulk",
            json={
                "filters": [], "alert_id": "not-a-uuid",
                "category": "Network", "subcategory": "Switch",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()

    def test_non_string_alert_id_is_400_not_500(
        self, client, authed_headers, blueprint_app
    ):
        # These bulk handlers read raw JSON (no Pydantic gate), unlike the list
        # path where AssetListParams pins alert_id to Optional[str]. A non-string
        # alert_id reaches ensure_uuid, whose UUID() raises AttributeError (not
        # ValueError) on a non-string — which handle_errors would answer with a
        # 500. Guard the type so it stays the 400 the #973/#999 contract promises.
        resp = client.post(
            "/api/assets/classify-bulk",
            json={
                "filters": [], "alert_id": 1234,
                "category": "Network", "subcategory": "Switch",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/assets/classify-bulk/clear
# ---------------------------------------------------------------------------

class TestBulkClearOverrides:
    """POST /api/assets/classify-bulk/clear — reset overrides to predicted."""

    def test_happy_path(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1", "a2"]
        blueprint_app.reclassify_service.clear_overrides_bulk.return_value = {"cleared": 2}

        resp = client.post(
            "/api/assets/classify-bulk/clear",
            json={"filters": [], "search": "printer"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == {"cleared": 2}
        blueprint_app.reclassify_service.clear_overrides_bulk.assert_called_once()

    def test_alert_scope_narrows_the_clear_target_set(
        self, client, authed_headers, blueprint_app
    ):
        # Same #1011 gap on the clear path: the alert scope must reach the
        # resolver so a clear launched from an alert drilldown resets exactly the
        # alert's assets, not every override in the fleet.
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.reclassify_service.clear_overrides_bulk.return_value = {"cleared": 1}

        resp = client.post(
            "/api/assets/classify-bulk/clear",
            json={"filters": [], "alert_id": "948e3f69-818b-4649-8f77-362048686475"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        kwargs = blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs
        assert kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"


# ---------------------------------------------------------------------------
# POST /api/assets/tags-bulk  (#1156)
# ---------------------------------------------------------------------------

class TestBulkTag:
    """POST /api/assets/tags-bulk — resolve filter, validate the tag, delegate.

    Shares _resolve_bulk_ids with the classify-bulk pair, so the filter/cap/
    alert contract is identical and asserted the same way here — a future
    refactor that gives this route its own resolution would show up as a
    failure in this class, not just in the classify one.
    """

    def test_happy_path(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1", "a2"]
        blueprint_app.tag_service.add_tag_bulk.return_value = {"tagged": 2, "created": 2}

        resp = client.post(
            "/api/assets/tags-bulk",
            json={
                "filters": [], "search": "printer",
                "tag_name": "needs-patching", "tag_color": "#ef4444",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == {"tagged": 2, "created": 2}
        blueprint_app.tag_service.add_tag_bulk.assert_called_once_with(
            asset_ids=["a1", "a2"],
            tag_name="needs-patching",
            tag_color="#ef4444",
            user="test-user-id",
        )

    def test_absent_color_falls_back_to_the_per_asset_default(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.add_tag_bulk.return_value = {"tagged": 1, "created": 1}

        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert blueprint_app.tag_service.add_tag_bulk.call_args.kwargs["tag_color"] == "#64748b"

    def test_tag_name_is_trimmed(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.add_tag_bulk.return_value = {"tagged": 1, "created": 1}

        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [], "tag_name": "  prod  "},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert blueprint_app.tag_service.add_tag_bulk.call_args.kwargs["tag_name"] == "prod"

    def test_over_cap_writes_nothing(self, client, authed_headers, blueprint_app):
        # Same cap, same message, same details payload as the classify routes —
        # they read one constant, and the dialog surfaces body.message verbatim.
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["x"] * 1001

        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["details"]["cap"] == 1000
        assert body["message"] == (
            "Filter matches more than 1000 assets. Narrow your filter and try again."
        )
        blueprint_app.tag_service.add_tag_bulk.assert_not_called()

    def test_empty_match_is_noop(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = []
        blueprint_app.tag_service.add_tag_bulk.return_value = {"tagged": 0, "created": 0}

        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"] == {"tagged": 0, "created": 0}

    def test_malformed_filter_returns_400_without_calling_service(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [{"bogus": "shape"}], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()
        blueprint_app.tag_service.add_tag_bulk.assert_not_called()

    @pytest.mark.parametrize("payload", [
        {"filters": []},                                   # tag_name absent
        {"filters": [], "tag_name": None},                 # explicit null
        {"filters": [], "tag_name": "   "},                # blank after strip
        {"filters": [], "tag_name": 42},                   # not a string
        {"filters": [], "tag_name": "x" * 101},            # column is varchar(100)
        {"filters": [], "tag_name": "p", "tag_color": "red"},        # not hex
        {"filters": [], "tag_name": "p", "tag_color": "#12345"},     # short hex
        {"filters": [], "tag_name": "p", "tag_color": "#1234567"},   # varchar(7) overflow
        {"filters": [], "tag_name": "p", "tag_color": 0x64748b},     # not a string
    ])
    def test_bad_tag_input_is_400_before_resolution(
        self, client, authed_headers, blueprint_app, payload
    ):
        # Validation runs first, so a bad tag never costs a filter query — and
        # never reaches a varchar column that would answer with a 500.
        resp = client.post(
            "/api/assets/tags-bulk", json=payload, headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()
        blueprint_app.tag_service.add_tag_bulk.assert_not_called()

    def test_max_length_tag_name_is_accepted(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.add_tag_bulk.return_value = {"tagged": 1, "created": 1}

        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [], "tag_name": "x" * 100},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200

    def test_alert_scope_narrows_the_target_set_not_the_fleet(
        self, client, authed_headers, blueprint_app
    ):
        # The #1011 contract, inherited from _resolve_bulk_ids: under an alert
        # drilldown filters=[] is the only scope, so dropping alert_id would tag
        # every asset in the fleet while the button read the alert's size.
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.add_tag_bulk.return_value = {"tagged": 1, "created": 1}

        resp = client.post(
            "/api/assets/tags-bulk",
            json={
                "filters": [],
                "alert_id": "948E3F69-818B-4649-8F77-362048686475",
                "tag_name": "prod",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        kwargs = blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs
        assert kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"

    def test_malformed_alert_id_is_400_without_resolving(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [], "alert_id": "not-a-uuid", "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()

    def test_service_value_error_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.add_tag_bulk.side_effect = ValueError(
            "Tag name cannot be empty"
        )

        resp = client.post(
            "/api/assets/tags-bulk",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/assets/tags-bulk/candidates
# ---------------------------------------------------------------------------

# One well-formed Explorer filter, as buildFilters() emits it.
HOSTNAME_FILTER = {
    "field_id": {"kind": "static", "name": "Hostname"},
    "op": "eq",
    "value": "web01",
}


class TestBulkTagCandidates:
    """GET /api/assets/tags-bulk/candidates — the picker behind bulk removal.

    Same filter scope, same resolver, same cap as the action it serves: if the
    filter is too wide to act on it is too wide to pick for, so the dialog can
    surface one message instead of a picker that loads and an action that then
    fails.
    """

    def test_happy_path(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1", "a2"]
        blueprint_app.tag_service.get_bulk_tag_candidates.return_value = [
            {"tag_name": "needs-patching", "count": 2},
            {"tag_name": "prod", "count": 1},
        ]

        resp = client.get(
            "/api/assets/tags-bulk/candidates?search=printer",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["candidates"] == [
            {"tag_name": "needs-patching", "count": 2},
            {"tag_name": "prod", "count": 1},
        ]
        # Scoped to the resolved set, not to the fleet.
        blueprint_app.tag_service.get_bulk_tag_candidates.assert_called_once_with(["a1", "a2"])
        assert blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs["search"] == "printer"

    def test_filters_arrive_json_encoded_in_the_query_string(
        self, client, authed_headers, blueprint_app
    ):
        # A GET cannot carry the action's JSON body, so filters ride the query
        # string like /assets/values takes them — decoded here, then validated
        # by the same _resolve_bulk_ids the POST routes use.
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.get_bulk_tag_candidates.return_value = []

        resp = client.get(
            "/api/assets/tags-bulk/candidates",
            query_string={"filters": json.dumps([HOSTNAME_FILTER])},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        kwargs = blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs
        assert kwargs["filters"] == [HOSTNAME_FILTER]

    def test_absent_filters_is_an_unfiltered_scope_not_an_error(
        self, client, authed_headers, blueprint_app
    ):
        # Explorer with no filters at all: legitimate, and the cap is what keeps
        # it from being a fleet-wide picker.
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.get_bulk_tag_candidates.return_value = []

        resp = client.get(
            "/api/assets/tags-bulk/candidates", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs["filters"] == []

    def test_malformed_filters_json_returns_400_without_resolving(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.get(
            "/api/assets/tags-bulk/candidates",
            query_string={"filters": "{not json"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()

    def test_filters_that_are_not_an_array_return_400(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.get(
            "/api/assets/tags-bulk/candidates",
            query_string={"filters": json.dumps({"field_id": "x"})},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()

    def test_malformed_filter_shape_returns_400_without_resolving(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.get(
            "/api/assets/tags-bulk/candidates",
            query_string={"filters": json.dumps([{"bogus": "shape"}])},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()

    def test_over_cap_is_the_same_400_as_the_action(
        self, client, authed_headers, blueprint_app
    ):
        # The point of resolving through _resolve_bulk_ids: the dialog surfaces
        # one consistent message rather than loading a picker whose action then
        # fails on the very same cap.
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["x"] * 1001

        resp = client.get(
            "/api/assets/tags-bulk/candidates", headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["details"]["cap"] == 1000
        assert body["message"] == (
            "Filter matches more than 1000 assets. Narrow your filter and try again."
        )
        blueprint_app.tag_service.get_bulk_tag_candidates.assert_not_called()

    def test_no_manual_tags_is_an_empty_list_not_an_error(
        self, client, authed_headers, blueprint_app
    ):
        # Every matched asset carries only rule-applied tags (or none at all).
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1", "a2"]
        blueprint_app.tag_service.get_bulk_tag_candidates.return_value = []

        resp = client.get(
            "/api/assets/tags-bulk/candidates", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"] == {"candidates": []}

    def test_empty_match_still_answers_200(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = []
        blueprint_app.tag_service.get_bulk_tag_candidates.return_value = []

        resp = client.get(
            "/api/assets/tags-bulk/candidates", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.tag_service.get_bulk_tag_candidates.assert_called_once_with([])

    def test_alert_scope_narrows_the_candidate_set(
        self, client, authed_headers, blueprint_app
    ):
        # #1011 again: under an alert drilldown filters=[] is the only scope, so
        # dropping alert_id would list the fleet's manual tags with fleet counts
        # — and the counts are what the confirm button promises.
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.get_bulk_tag_candidates.return_value = []

        resp = client.get(
            "/api/assets/tags-bulk/candidates",
            query_string={"alert_id": "948E3F69-818B-4649-8F77-362048686475"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        kwargs = blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs
        assert kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"

    def test_malformed_alert_id_is_400_without_resolving(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.get(
            "/api/assets/tags-bulk/candidates",
            query_string={"alert_id": "not-a-uuid"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/assets/tags-bulk/remove
# ---------------------------------------------------------------------------

class TestBulkUntag:
    """POST /api/assets/tags-bulk/remove — the undo half of tags-bulk.

    Same _resolve_bulk_ids contract as its three siblings, asserted here too so
    a future refactor that gives this route its own resolution fails in this
    class rather than only in the classify one.
    """

    def test_happy_path(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1", "a2"]
        blueprint_app.tag_service.remove_tag_bulk.return_value = {"removed": 1}

        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [], "search": "printer", "tag_name": "needs-patching"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        # Two assets matched the filter, one carried the tag: the other is
        # silently skipped, not an error.
        assert body["data"] == {"removed": 1}
        blueprint_app.tag_service.remove_tag_bulk.assert_called_once_with(
            asset_ids=["a1", "a2"],
            tag_name="needs-patching",
            user="test-user-id",
        )

    def test_tag_name_is_trimmed(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.remove_tag_bulk.return_value = {"removed": 1}

        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [], "tag_name": "  prod  "},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert blueprint_app.tag_service.remove_tag_bulk.call_args.kwargs["tag_name"] == "prod"

    @pytest.mark.parametrize("payload", [
        {"filters": []},                        # tag_name absent
        {"filters": [], "tag_name": None},      # explicit null
        {"filters": [], "tag_name": "   "},     # blank after strip
        {"filters": [], "tag_name": 42},        # not a string
    ])
    def test_bad_tag_name_is_400_before_resolution(
        self, client, authed_headers, blueprint_app, payload
    ):
        # 400, not 422 — this codebase's ValidationError maps to 400. Validated
        # first, so a bad tag never costs a filter query.
        resp = client.post(
            "/api/assets/tags-bulk/remove", json=payload, headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()
        blueprint_app.tag_service.remove_tag_bulk.assert_not_called()

    def test_over_cap_writes_nothing(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["x"] * 1001

        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["details"]["cap"] == 1000
        assert body["message"] == (
            "Filter matches more than 1000 assets. Narrow your filter and try again."
        )
        blueprint_app.tag_service.remove_tag_bulk.assert_not_called()

    def test_nothing_carried_the_tag_is_a_200_zero(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1", "a2"]
        blueprint_app.tag_service.remove_tag_bulk.return_value = {"removed": 0}

        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"] == {"removed": 0}

    def test_empty_match_is_noop(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = []
        blueprint_app.tag_service.remove_tag_bulk.return_value = {"removed": 0}

        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"] == {"removed": 0}

    def test_malformed_filter_returns_400_without_calling_service(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [{"bogus": "shape"}], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()
        blueprint_app.tag_service.remove_tag_bulk.assert_not_called()

    def test_alert_scope_narrows_the_target_set_not_the_fleet(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.remove_tag_bulk.return_value = {"removed": 1}

        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={
                "filters": [],
                "alert_id": "948E3F69-818B-4649-8F77-362048686475",
                "tag_name": "prod",
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        kwargs = blueprint_app.asset_service.get_matching_asset_ids.call_args.kwargs
        assert kwargs["alert_id"] == "948e3f69-818b-4649-8f77-362048686475"

    def test_malformed_alert_id_is_400_without_resolving(
        self, client, authed_headers, blueprint_app
    ):
        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [], "alert_id": "not-a-uuid", "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.asset_service.get_matching_asset_ids.assert_not_called()

    def test_service_value_error_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.asset_service.get_matching_asset_ids.return_value = ["a1"]
        blueprint_app.tag_service.remove_tag_bulk.side_effect = ValueError(
            "Tag name cannot be empty"
        )

        resp = client.post(
            "/api/assets/tags-bulk/remove",
            json={"filters": [], "tag_name": "prod"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400
