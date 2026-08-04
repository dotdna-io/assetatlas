# SPDX-License-Identifier: AGPL-3.0-or-later
"""
End-to-end integration tests for the posture rules API.

Hits real endpoints (/api/posture-rules/*) against a live database.
Each test that writes data cleans up after itself via try/finally.

`live_app` and `live_admin_headers` are supplied by
tests/integration/conftest.py (session-scoped).
"""

import json
import uuid

import pytest

pytestmark = pytest.mark.integration


def test_seed_categories_present(live_client, live_admin_headers):
    """The 5 seed categories must be returned by GET /api/posture-rules."""
    resp = live_client.get("/api/posture-rules", headers=live_admin_headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()["data"]
    ids = {c["id"] for c in body["categories"]}
    assert ids == {
        "secured",
        "vulnerability_scanning",
        "domain_joined",
        "device_management",
        "monitored",
    }


def test_sub_rule_crud_roundtrip(live_client, live_admin_headers):
    """Create → update → toggle → delete a sub-rule via the API."""
    unique_name = f"integration-{uuid.uuid4()}"

    # Create
    create_resp = live_client.post(
        "/api/posture-rules/secured/sub-rules",
        json={
            "name": unique_name,
            "target_filters": [
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}
            ],
            "compliance_criteria": {
                "logic": "any",
                "conditions": [
                    {
                        "field_id": {"kind": "static", "name": "Data Source"},
                        "operator": "contains",
                        "value": "EDR",
                    }
                ],
            },
        },
        headers=live_admin_headers,
    )
    assert create_resp.status_code == 201, create_resp.get_data(as_text=True)
    sub_rule_id = create_resp.get_json()["data"]["id"]

    try:
        # Update
        upd = live_client.put(
            f"/api/posture-rules/sub-rules/{sub_rule_id}",
            json={
                "name": f"renamed-{unique_name}",
                "enabled": False,
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [
                        {
                            "field_id": {"kind": "static", "name": "Data Source"},
                            "operator": "contains",
                            "value": "EDR",
                        }
                    ],
                },
            },
            headers=live_admin_headers,
        )
        assert upd.status_code == 200, upd.get_data(as_text=True)
        assert upd.get_json()["data"]["enabled"] is False

        # Toggle back on
        tog = live_client.post(
            f"/api/posture-rules/sub-rules/{sub_rule_id}/toggle",
            json={"enabled": True},
            headers=live_admin_headers,
        )
        assert tog.status_code == 200, tog.get_data(as_text=True)
        assert tog.get_json()["data"]["enabled"] is True
    finally:
        # Delete
        d = live_client.delete(
            f"/api/posture-rules/sub-rules/{sub_rule_id}",
            headers=live_admin_headers,
        )
        assert d.status_code == 204, d.get_data(as_text=True)


def test_preview_returns_counts(live_client, live_admin_headers):
    """Preview endpoint must return {matched, pass, fail} shape."""
    resp = live_client.post(
        "/api/posture-rules/sub-rules/preview",
        json={
            "target_filters": [
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}
            ],
            "compliance_criteria": {
                "logic": "any",
                "conditions": [
                    {
                        "field_id": {"kind": "static", "name": "Data Source"},
                        "operator": "contains",
                        "value": "EDR",
                    }
                ],
            },
        },
        headers=live_admin_headers,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    assert set(data.keys()) == {"matched", "pass", "fail"}


def test_execute_writes_posture_rules(live_client, live_admin_headers, live_db):
    """POST /execute populates assets.posture_rules with all 5 category keys."""
    asset_id = str(uuid.uuid4())
    live_db.execute_query(
        "INSERT INTO assets (id, category) VALUES (%(id)s::uuid, 'workstation')",
        {"id": asset_id},
        fetch=False,
    )
    try:
        resp = live_client.post("/api/posture-rules/execute", headers=live_admin_headers)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["data"]["updated_count"] >= 1

        rows = live_db.execute_query(
            "SELECT posture_rules FROM assets WHERE id = %(id)s::uuid",
            {"id": asset_id},
        )
        assert len(rows) == 1
        pr = rows[0]["posture_rules"]
        assert set(pr.keys()) == {
            "secured",
            "vulnerability_scanning",
            "domain_joined",
            "device_management",
            "monitored",
        }
        for v in pr.values():
            assert isinstance(v, dict)
            assert v.get("state") in ("true", "false", "n/a")
            assert "output" in v
    finally:
        live_db.execute_query(
            "DELETE FROM assets WHERE id = %(id)s::uuid",
            {"id": asset_id},
            fetch=False,
        )


def test_patch_category_applicable_categories(live_client, live_admin_headers, live_db):
    """PATCH /categories/{id} updates applicable_categories."""
    # Record the original so we can restore after.
    original = live_db.execute_query(
        "SELECT applicable_categories FROM posture_categories WHERE id = %(id)s",
        {"id": "secured"},
    )[0]["applicable_categories"]

    try:
        resp = live_client.patch(
            "/api/posture-rules/categories/secured",
            json={"applicable_categories": ["server"]},
            headers=live_admin_headers,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["data"]["applicable_categories"] == ["server"]

        # Verify it persisted.
        row = live_db.execute_query(
            "SELECT applicable_categories FROM posture_categories WHERE id = %(id)s",
            {"id": "secured"},
        )[0]
        assert row["applicable_categories"] == ["server"]
    finally:
        live_db.execute_query(
            "UPDATE posture_categories SET applicable_categories = %(v)s::jsonb WHERE id = %(id)s",
            {"id": "secured", "v": json.dumps(original)},
            fetch=False,
        )


def test_patch_category_rejects_unknown_token(live_client, live_admin_headers):
    """PATCH with an unknown asset-category token should return 400."""
    resp = live_client.patch(
        "/api/posture-rules/categories/secured",
        json={"applicable_categories": ["server", "toaster"]},
        headers=live_admin_headers,
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)


def test_patch_category_404_on_unknown_category(live_client, live_admin_headers):
    resp = live_client.patch(
        "/api/posture-rules/categories/does_not_exist",
        json={"applicable_categories": ["server"]},
        headers=live_admin_headers,
    )
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_execute_sets_last_evaluated_at(live_client, live_admin_headers):
    """After /execute, every category in the list response has a non-null last_evaluated_at."""
    resp = live_client.post("/api/posture-rules/execute", headers=live_admin_headers)
    assert resp.status_code == 200

    list_resp = live_client.get("/api/posture-rules", headers=live_admin_headers)
    body = list_resp.get_json()["data"]
    for cat in body["categories"]:
        assert cat["last_evaluated_at"] is not None


def test_applicable_categories_forces_na(live_client, live_admin_headers, live_db):
    """An asset whose category is outside applicable_categories must be n/a regardless."""
    asset_id = str(uuid.uuid4())
    live_db.execute_query(
        "INSERT INTO assets (id, category) VALUES (%(id)s::uuid, 'iot')",
        {"id": asset_id},
        fetch=False,
    )
    original = live_db.execute_query(
        "SELECT applicable_categories FROM posture_categories WHERE id = 'secured'"
    )[0]["applicable_categories"]

    try:
        # Constrain secured to server, workstation only.
        patch_resp = live_client.patch(
            "/api/posture-rules/categories/secured",
            json={"applicable_categories": ["server", "workstation"]},
            headers=live_admin_headers,
        )
        assert patch_resp.status_code == 200

        exec_resp = live_client.post("/api/posture-rules/execute", headers=live_admin_headers)
        assert exec_resp.status_code == 200

        rows = live_db.execute_query(
            "SELECT posture_rules FROM assets WHERE id = %(id)s::uuid",
            {"id": asset_id},
        )
        ep = rows[0]["posture_rules"]["secured"]
        assert ep["state"] == "n/a"
        assert ep["output"] is None
    finally:
        live_db.execute_query(
            "DELETE FROM assets WHERE id = %(id)s::uuid",
            {"id": asset_id},
            fetch=False,
        )
        live_db.execute_query(
            "UPDATE posture_categories SET applicable_categories = %(v)s::jsonb WHERE id = 'secured'",
            {"v": json.dumps(original)},
            fetch=False,
        )


@pytest.mark.parametrize("field,value", [
    ("Category", "server,workstation"),   # STRING field
    ("Hostname", "host1,host2,host3"),    # STRING_ARRAY field
])
def test_preview_in_operator(live_client, live_admin_headers, field, value):
    """POST /preview with `in` operator on STRING and STRING_ARRAY fields."""
    resp = live_client.post(
        "/api/posture-rules/sub-rules/preview",
        json={
            "target_filters": [
                {"field_id": {"kind": "static", "name": field}, "operator": "any of", "value": value}
            ],
            "compliance_criteria": {
                "logic": "any",
                "conditions": [
                    {"field_id": {"kind": "static", "name": "Domain"}, "operator": "is not empty", "value": ""},
                ],
            },
        },
        headers=live_admin_headers,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    assert set(data.keys()) == {"matched", "pass", "fail"}
    assert data["matched"] >= 0


def test_by_asset_category_shape_in_response(live_client, live_admin_headers):
    """GET /api/posture-rules: stats.by_asset_category must be {str: {covered, total}}."""
    resp = live_client.get("/api/posture-rules", headers=live_admin_headers)
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    for cat in body["categories"]:
        stats = cat["stats"]
        assert "by_asset_category" in stats
        assert isinstance(stats["by_asset_category"], dict)
        for asset_cat, coverage in stats["by_asset_category"].items():
            assert isinstance(asset_cat, str)
            assert isinstance(coverage, dict)
            assert set(coverage.keys()) == {"covered", "total"}
            assert isinstance(coverage["covered"], int)
            assert isinstance(coverage["total"], int)
            assert coverage["covered"] <= coverage["total"]


def test_reorder_sub_rules_roundtrip(live_client, live_admin_headers, live_db):
    """Create 3 sub-rules, reorder them, verify display_order persisted."""
    ids = []
    try:
        for i in range(3):
            r = live_client.post(
                "/api/posture-rules/secured/sub-rules",
                json={
                    "name": f"order-test-{uuid.uuid4()}-{i}",
                    "target_filters": [
                        {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
                    ],
                    "compliance_criteria": {
                        "logic": "any",
                        "conditions": [
                            {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
                        ],
                    },
                },
                headers=live_admin_headers,
            )
            assert r.status_code == 201
            ids.append(r.get_json()["data"]["id"])

        # Reverse the order
        resp = live_client.put(
            "/api/posture-rules/secured/sub-rules/order",
            json={"ordered_ids": list(reversed(ids))},
            headers=live_admin_headers,
        )
        assert resp.status_code == 204, resp.get_data(as_text=True)

        # Verify via the DB
        rows = live_db.execute_query(
            "SELECT id, display_order FROM posture_sub_rules WHERE id = ANY(%(ids)s::uuid[]) ORDER BY display_order",
            {"ids": ids},
        )
        ordered_now = [str(r["id"]) for r in rows]
        assert ordered_now == list(reversed(ids))
    finally:
        for sid in ids:
            live_client.delete(
                f"/api/posture-rules/sub-rules/{sid}",
                headers=live_admin_headers,
            )


def test_preview_output_endpoint(live_client, live_admin_headers):
    """POST /sub-rules/preview-output returns shape {matched, passed, samples}."""
    resp = live_client.post(
        "/api/posture-rules/sub-rules/preview-output",
        json={
            "target_filters": [
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}
            ],
            "compliance_criteria": {
                "logic": "any",
                "conditions": [
                    {"field_id": {"kind": "static", "name": "Domain"}, "operator": "is not empty", "value": ""},
                ],
            },
            "output_mode": "field",
            "output_field_id": {"kind": "static", "name": "Domain"},
        },
        headers=live_admin_headers,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    assert set(data.keys()) == {"matched", "passed", "samples"}
    assert isinstance(data["samples"], list)
    if data["samples"]:
        sample = data["samples"][0]
        assert set(sample.keys()) == {"hostname", "output"}


def test_history_written_on_create_and_update(live_client, live_admin_headers, live_db):
    """Creating then updating a sub-rule must produce two history entries."""
    create_resp = live_client.post(
        "/api/posture-rules/secured/sub-rules",
        json={
            "name": f"history-test-{uuid.uuid4()}",
            "target_filters": [
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}
            ],
            "compliance_criteria": {
                "logic": "any",
                "conditions": [
                    {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
                ],
            },
        },
        headers=live_admin_headers,
    )
    assert create_resp.status_code == 201
    sub_rule_id = create_resp.get_json()["data"]["id"]

    try:
        upd = live_client.put(
            f"/api/posture-rules/sub-rules/{sub_rule_id}",
            json={
                "name": f"history-test-renamed-{uuid.uuid4()}",
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [
                        {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
                    ],
                },
            },
            headers=live_admin_headers,
        )
        assert upd.status_code == 200

        history_resp = live_client.get(
            f"/api/posture-rules/sub-rules/{sub_rule_id}/history",
            headers=live_admin_headers,
        )
        assert history_resp.status_code == 200
        entries = history_resp.get_json()["data"]["entries"]
        actions = [e["action"] for e in entries]
        # Most recent first
        assert actions[0] == "update"
        assert actions[-1] == "create"
    finally:
        live_client.delete(
            f"/api/posture-rules/sub-rules/{sub_rule_id}",
            headers=live_admin_headers,
        )
