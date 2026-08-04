# SPDX-License-Identifier: AGPL-3.0-or-later
class TestUnifiedFieldsBlueprint:
    def test_list_returns_fields(self, client, auth_headers, blueprint_app):
        blueprint_app.unified_fields_service.list.return_value = [
            {"id": "1", "name": "lentity", "function": "coalesce",
             "sources": ["ext:A:lentity", "ext:B:lentity"], "data_type": "string",
             "encoded_key": "virtual:lentity", "created_at": None, "updated_at": None},
        ]
        resp = client.get("/api/fields/unified", headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["fields"][0]["encoded_key"] == "virtual:lentity"

    def test_create_requires_admin(self, client, auth_headers, _prime_regular_user):
        resp = client.post(
            "/api/fields/unified",
            json={"name": "lentity", "function": "coalesce",
                  "sources": ["ext:A:lentity", "ext:B:lentity"]},
            headers=auth_headers("user"),
        )
        assert resp.status_code == 403

    def test_create_admin_ok(self, client, auth_headers, blueprint_app):
        blueprint_app.unified_fields_service.create.return_value = {
            "id": "1", "name": "lentity", "function": "coalesce",
            "sources": ["ext:A:lentity", "ext:B:lentity"], "data_type": "string",
            "encoded_key": "virtual:lentity", "created_at": None, "updated_at": None}
        resp = client.post(
            "/api/fields/unified",
            json={"name": "lentity", "function": "coalesce",
                  "sources": ["ext:A:lentity", "ext:B:lentity"]},
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["success"] is True
        assert body["field"]["encoded_key"] == "virtual:lentity"

    def test_preview_admin_ok(self, client, auth_headers, blueprint_app):
        blueprint_app.unified_fields_service.preview.return_value = {
            "data_type": "string",
            "rows": [{"asset_id": "a1", "hostname": "host1",
                      "source_values": {"ext:A:lentity": "X"}, "result": "X"}]}
        resp = client.post(
            "/api/fields/unified/preview",
            json={"function": "coalesce", "sources": ["ext:A:lentity", "ext:B:lentity"]},
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data_type"] == "string"
        assert body["rows"][0]["result"] == "X"
