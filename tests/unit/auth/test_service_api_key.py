# SPDX-License-Identifier: AGPL-3.0-or-later
"""Service-API-key decorator tests."""
from __future__ import annotations

import pytest
from flask import Flask, jsonify

from src.auth.api_key_auth import require_service_api_key


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv(
        "ASSETATLAS_BRIDGE_API_KEY",
        "test-bridge-key-padding-to-pass-min-length",
    )
    app = Flask(__name__)

    @app.route("/protected")
    @require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
    def protected():
        return jsonify(ok=True)

    return app


def test_missing_header_401(app):
    client = app.test_client()
    rv = client.get("/protected")
    assert rv.status_code == 401


def test_wrong_key_403(app):
    client = app.test_client()
    rv = client.get("/protected", headers={"X-Service-Key": "wrong"})
    assert rv.status_code == 403


def test_correct_key_passes(app):
    client = app.test_client()
    rv = client.get(
        "/protected",
        headers={
            "X-Service-Key":
            "test-bridge-key-padding-to-pass-min-length",
        },
    )
    assert rv.status_code == 200
    assert rv.get_json() == {"ok": True}


def test_unset_env_returns_503(monkeypatch):
    """When the env var is unset (or too short), the decorator should
    fail closed — return 503 rather than accept any value."""
    monkeypatch.delenv("ASSETATLAS_BRIDGE_API_KEY", raising=False)
    app = Flask(__name__)

    @app.route("/protected")
    @require_service_api_key("ASSETATLAS_BRIDGE_API_KEY")
    def protected():
        return jsonify(ok=True)

    client = app.test_client()
    rv = client.get(
        "/protected", headers={"X-Service-Key": "anything"},
    )
    assert rv.status_code == 503
