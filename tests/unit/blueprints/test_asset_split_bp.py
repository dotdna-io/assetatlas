# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for asset-split endpoints (admin-only, over-merge remediation).

Endpoints tested:
- GET  /api/assets/<id>/split-preview  — dry-run preview of a split
- POST /api/assets/<id>/split          — execute a split

Mirrors test_asset_edit_bp.py: `client` (function-scoped, resets MagicMock
services between tests), `authed_headers` (primes the user cache to the given
role, so `require_admin()` genuinely 403s a non-admin), `blueprint_app`
session-scoped app with `asset_split_service` mocked.

Both handlers stamp `next_run_at` via
`_get_pipeline_next_run(current_app.pipeline_service)`. On a bare MagicMock,
`pipeline_service._get_setting(...)` returns a (truthy) MagicMock, so the
helper's `if not enabled: return None` guard is skipped and it walks into the
scheduler/interval branch, which can return a non-JSON-serializable
MagicMock and fail response serialization. The happy-path tests pin
`pipeline_service._get_setting.return_value = None` so the helper
short-circuits to `None` (schedule disabled) before that branch is reached.
"""

AID = "438c3df4-3001-4f62-9115-452f58b08070"
BODY = {
    "bridge": ["lateral"],
    "primary_cluster": ["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
    "clusters": [
        ["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
        ["bsc_camera_side2", "ec:71:db:cb:20:0b"],
    ],
}


def test_preview_requires_admin(client, authed_headers):
    resp = client.get(
        f"/api/assets/{AID}/split-preview", headers=authed_headers("user")
    )
    assert resp.status_code == 403


def test_split_requires_admin(client, authed_headers):
    resp = client.post(
        f"/api/assets/{AID}/split", json=BODY, headers=authed_headers("user")
    )
    assert resp.status_code == 403


def test_preview_happy_path(client, authed_headers, blueprint_app):
    blueprint_app.asset_split_service.preview.return_value = {
        "bridge": "lateral",
        "clusters": BODY["clusters"],
        "would_split": True,
        "warnings": [],
    }
    blueprint_app.pipeline_service.get_current_status.return_value = {"status": "idle"}
    blueprint_app.pipeline_service._get_setting.return_value = None
    resp = client.get(
        f"/api/assets/{AID}/split-preview", headers=authed_headers("admin")
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["bridge"] == "lateral"


def test_split_validation_400_on_single_cluster(client, authed_headers):
    bad = {**BODY, "clusters": [BODY["clusters"][0]]}  # min_length=2 violated
    resp = client.post(
        f"/api/assets/{AID}/split", json=bad, headers=authed_headers("admin")
    )
    assert resp.status_code == 400


def test_split_409ish_when_running_maps_to_400(client, authed_headers, blueprint_app):
    blueprint_app.asset_split_service.execute.side_effect = ValueError(
        "a pipeline run is in progress; try again shortly"
    )
    resp = client.post(
        f"/api/assets/{AID}/split", json=BODY, headers=authed_headers("admin")
    )
    assert resp.status_code == 400


def test_split_happy_path_202(client, authed_headers, blueprint_app):
    blueprint_app.asset_split_service.execute.return_value = {
        "primary_id": AID,
        "split_into": [AID, "new-uuid"],
    }
    blueprint_app.pipeline_service.get_current_status.return_value = {"status": "idle"}
    blueprint_app.pipeline_service._get_setting.return_value = None
    resp = client.post(
        f"/api/assets/{AID}/split", json=BODY, headers=authed_headers("admin")
    )
    assert resp.status_code == 202
    assert resp.get_json()["data"]["primary_id"] == AID
