# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone

import pytest

from src.api.blueprints.saved_filters.repository import SavedFilterRow
from src.api.middleware.error_handler import ConflictError, ForbiddenError

_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _row(**over):
    base = dict(id="fid", owner_id="test-user-id", name="Servers",
                description=None, payload={"version": 1}, visibility="private",
                created_at=_NOW, updated_at=_NOW)
    base.update(over)
    return SavedFilterRow(**base)


def _to_dict(row, *, viewer_id):
    from src.api.blueprints.saved_filters.service import SavedFiltersService
    return SavedFiltersService.to_dict(row, viewer_id=viewer_id)


class TestAuth:
    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/saved-filters"),
        ("GET", "/api/saved-filters"),
        ("GET", "/api/saved-filters/fid"),
        ("PUT", "/api/saved-filters/fid"),
        ("DELETE", "/api/saved-filters/fid"),
    ])
    def test_requires_auth(self, client, method, path):
        kwargs = {"json": {}} if method in ("POST", "PUT") else {}
        resp = getattr(client, method.lower())(path, **kwargs)
        assert resp.status_code == 401


class TestCreate:
    def test_creates_filter(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.create.return_value = _row()
        svc.to_dict.side_effect = _to_dict

        resp = client.post("/api/saved-filters",
                           json={"name": "Servers", "payload": {"version": 1}},
                           headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["id"] == "fid"
        # non-admin user -> is_admin False forwarded to the service
        _, kwargs = svc.create.call_args
        assert kwargs["is_admin"] is False
        assert kwargs["owner_id"] == "test-user-id"

    def test_admin_role_forwards_is_admin_true(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.create.return_value = _row()
        svc.to_dict.side_effect = _to_dict
        client.post("/api/saved-filters",
                    json={"name": "S", "payload": {"version": 1}},
                    headers=authed_headers("admin"))
        _, kwargs = svc.create.call_args
        assert kwargs["is_admin"] is True

    def test_rejects_blank_name_422(self, client, authed_headers, blueprint_app):
        resp = client.post("/api/saved-filters",
                           json={"name": "", "payload": {"version": 1}},
                           headers=authed_headers("user"))
        assert resp.status_code in (400, 422)

    def test_org_forbidden_for_non_admin_propagates_403(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.create.side_effect = ForbiddenError("nope")
        resp = client.post("/api/saved-filters",
                           json={"name": "S", "payload": {"version": 1}, "visibility": "org"},
                           headers=authed_headers("user"))
        assert resp.status_code == 403

    def test_duplicate_name_409(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.create.side_effect = ConflictError("dup")
        resp = client.post("/api/saved-filters",
                           json={"name": "S", "payload": {"version": 1}},
                           headers=authed_headers("user"))
        assert resp.status_code == 409


class TestList:
    def test_lists_grouped(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.list_for_user.return_value = {"mine": [_row()], "shared_with_me": [], "library": []}
        svc.to_dict.side_effect = _to_dict
        resp = client.get("/api/saved-filters", headers=authed_headers("user"))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert len(data["mine"]) == 1 and data["shared_with_me"] == [] and data["library"] == []

    def test_list_returns_library_group(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.list_for_user.return_value = {
            "mine": [], "shared_with_me": [],
            "library": [_row(owner_id="other", visibility="org", name="Fleet")],
        }
        svc.to_dict.side_effect = _to_dict
        resp = client.get("/api/saved-filters", headers=authed_headers("user"))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert len(data["library"]) == 1
        assert data["library"][0]["access"] == "org" and data["library"][0]["can_edit"] is False


class TestGet:
    def test_404_when_not_owned(self, client, authed_headers, blueprint_app):
        blueprint_app.saved_filters_service.get.return_value = None
        resp = client.get("/api/saved-filters/fid", headers=authed_headers("user"))
        assert resp.status_code == 404

    def test_returns_filter(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.get.return_value = _row()
        svc.to_dict.side_effect = _to_dict
        resp = client.get("/api/saved-filters/fid", headers=authed_headers("user"))
        assert resp.status_code == 200
        assert resp.get_json()["data"]["id"] == "fid"

    def test_non_owner_can_load_link_filter(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.get.return_value = _row(owner_id="other-user", visibility="link")
        svc.to_dict.side_effect = _to_dict
        resp = client.get("/api/saved-filters/fid", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["access"] == "link" and body["can_edit"] is False


class TestUpdate:
    def test_404_when_missing(self, client, authed_headers, blueprint_app):
        blueprint_app.saved_filters_service.update.return_value = None
        resp = client.put("/api/saved-filters/fid", json={"name": "New"},
                          headers=authed_headers("user"))
        assert resp.status_code == 404

    def test_updates(self, client, authed_headers, blueprint_app):
        svc = blueprint_app.saved_filters_service
        svc.update.return_value = _row(name="New")
        svc.to_dict.side_effect = _to_dict
        resp = client.put("/api/saved-filters/fid", json={"name": "New"},
                          headers=authed_headers("user"))
        assert resp.status_code == 200
        assert resp.get_json()["data"]["name"] == "New"


class TestDelete:
    def test_404_when_missing(self, client, authed_headers, blueprint_app):
        blueprint_app.saved_filters_service.delete.return_value = False
        resp = client.delete("/api/saved-filters/fid", headers=authed_headers("user"))
        assert resp.status_code == 404

    def test_deletes(self, client, authed_headers, blueprint_app):
        blueprint_app.saved_filters_service.delete.return_value = True
        resp = client.delete("/api/saved-filters/fid", headers=authed_headers("user"))
        assert resp.status_code == 200
        assert resp.get_json()["data"]["ok"] is True
