# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for validation decorator utilities.

Uses a minimal Flask app (NOT the blueprint_app fixture) to exercise
validate_request, validate_query, and require_fields decorators.
"""

import json
import pytest
from typing import Optional

from flask import Flask, jsonify
from pydantic import BaseModel, Field

from src.api.utils.validation import validate_request, validate_query, require_fields
from src.api.middleware.error_handler import register_error_handlers


# ---------------------------------------------------------------------------
# Pydantic models used by the test routes
# ---------------------------------------------------------------------------

class ItemCreate(BaseModel):
    name: str = Field(min_length=1)
    quantity: int = Field(ge=0)


class ListParams(BaseModel):
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=100)
    search: Optional[str] = None


# ---------------------------------------------------------------------------
# Minimal Flask test app
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Create a tiny Flask app with decorated test routes."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    # Register the global error handlers so APIValidationError is caught
    register_error_handlers(app)

    @app.route("/items", methods=["POST"])
    @validate_request(ItemCreate)
    def create_item(validated_data: ItemCreate):
        return jsonify({"name": validated_data.name, "quantity": validated_data.quantity}), 201

    @app.route("/items", methods=["GET"])
    @validate_query(ListParams)
    def list_items(query_params: ListParams):
        return jsonify({
            "page": query_params.page,
            "limit": query_params.limit,
            "search": query_params.search,
        })

    @app.route("/simple", methods=["POST"])
    @require_fields(["name", "email"])
    def simple_create():
        return jsonify({"ok": True}), 200

    return app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# validate_request
# ---------------------------------------------------------------------------

class TestValidateRequest:

    def test_valid_body_passes(self, client):
        resp = client.post(
            "/items",
            data=json.dumps({"name": "Widget", "quantity": 5}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["name"] == "Widget"
        assert body["quantity"] == 5

    def test_missing_required_field_returns_400(self, client):
        resp = client.post(
            "/items",
            data=json.dumps({"name": "Widget"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_value_returns_400(self, client):
        resp = client.post(
            "/items",
            data=json.dumps({"name": "Widget", "quantity": -1}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_non_json_content_type_rejected(self, client):
        """Flask rejects non-JSON content types before the decorator runs."""
        resp = client.post(
            "/items",
            data="not json",
            content_type="text/plain",
        )
        # Flask returns 415 Unsupported Media Type for non-JSON posts
        assert resp.status_code == 415

    def test_malformed_json_returns_400(self, client):
        resp = client.post(
            "/items",
            data="{bad json",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_empty_name_returns_400(self, client):
        resp = client.post(
            "/items",
            data=json.dumps({"name": "", "quantity": 1}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_error_body_contains_error_key(self, client):
        resp = client.post(
            "/items",
            data=json.dumps({"name": "Widget"}),
            content_type="application/json",
        )
        body = resp.get_json()
        assert "error" in body
        assert body["error"] == "validation_error"


# ---------------------------------------------------------------------------
# validate_query
# ---------------------------------------------------------------------------

class TestValidateQuery:

    def test_defaults_apply(self, client):
        resp = client.get("/items")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["page"] == 1
        assert body["limit"] == 50
        assert body["search"] is None

    def test_custom_params(self, client):
        resp = client.get("/items?page=2&limit=10&search=hello")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["page"] == 2
        assert body["limit"] == 10
        assert body["search"] == "hello"

    def test_invalid_page_returns_400(self, client):
        resp = client.get("/items?page=0")
        assert resp.status_code == 400

    def test_limit_over_max_returns_400(self, client):
        resp = client.get("/items?limit=999")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# require_fields
# ---------------------------------------------------------------------------

class TestRequireFields:

    def test_all_fields_present_passes(self, client):
        resp = client.post(
            "/simple",
            data=json.dumps({"name": "Alice", "email": "a@b.com"}),
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_missing_field_returns_400(self, client):
        resp = client.post(
            "/simple",
            data=json.dumps({"name": "Alice"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_missing_field_error_lists_field_name(self, client):
        resp = client.post(
            "/simple",
            data=json.dumps({"name": "Alice"}),
            content_type="application/json",
        )
        body = resp.get_json()
        assert "email" in str(body)

    def test_non_json_content_type_rejected(self, client):
        """Flask rejects non-JSON content types before the decorator runs."""
        resp = client.post(
            "/simple",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 415
