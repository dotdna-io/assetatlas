# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the teams_bot blueprint — uses blueprint_app fixture
(see tests/conftest.py) which mocks the services."""
from __future__ import annotations

import datetime as _dt
import os
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def bridge_key(monkeypatch):
    monkeypatch.setenv(
        "ASSETATLAS_BRIDGE_API_KEY",
        "test-bridge-key-padding-to-pass-min-length",
    )


def test_initiate_requires_service_key(client):
    rv = client.post("/api/auth/teams-link/initiate", json={
        "aad_object_id": "aad-1", "tenant_id": "t-1",
        "display_name": "Alice",
    })
    assert rv.status_code == 401


def test_initiate_returns_link_url(client, blueprint_app):
    blueprint_app.teams_link_service = MagicMock()
    blueprint_app.teams_link_service.mint_link_token.return_value = (
        "https://aa.example/teams/link?token=abc",
        _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )
    rv = client.post(
        "/api/auth/teams-link/initiate",
        json={
            "aad_object_id": "aad-1", "tenant_id": "t-1",
            "display_name": "Alice",
        },
        headers={"X-Service-Key": os.environ["ASSETATLAS_BRIDGE_API_KEY"]},
    )
    assert rv.status_code == 200
    body = rv.get_json()["data"]
    assert body["link_url"].startswith("https://aa.example/teams/link")


def test_exchange_404_when_not_linked(client, blueprint_app):
    blueprint_app.teams_link_service = MagicMock()
    blueprint_app.teams_link_service.find_link.return_value = None
    rv = client.post(
        "/api/auth/teams-bridge/exchange",
        json={"aad_object_id": "aad-1", "tenant_id": "t-1"},
        headers={"X-Service-Key": os.environ["ASSETATLAS_BRIDGE_API_KEY"]},
    )
    assert rv.status_code == 404


def test_exchange_returns_jwt_when_linked(client, blueprint_app):
    blueprint_app.teams_link_service = MagicMock()
    user_uuid = uuid4()
    link = MagicMock(); link.user_id = user_uuid
    blueprint_app.teams_link_service.find_link.return_value = link
    rv = client.post(
        "/api/auth/teams-bridge/exchange",
        json={"aad_object_id": "aad-1", "tenant_id": "t-1"},
        headers={"X-Service-Key": os.environ["ASSETATLAS_BRIDGE_API_KEY"]},
    )
    assert rv.status_code == 200
    body = rv.get_json()["data"]
    assert body["access_token"]               # non-empty JWT string
    assert body["expires_in"] == 300
    assert body["user_id"] == str(user_uuid)


def test_get_conversation_session_404(client, blueprint_app):
    blueprint_app.teams_link_service = MagicMock()
    blueprint_app.teams_link_service.repo.find_conversation_session\
        .return_value = None
    rv = client.get(
        "/api/auth/teams-bridge/conversation",
        query_string={"conversation_id": "conv-x"},
        headers={"X-Service-Key": os.environ["ASSETATLAS_BRIDGE_API_KEY"]},
    )
    assert rv.status_code == 404


def test_get_conversation_session_returns_mapping(client, blueprint_app):
    blueprint_app.teams_link_service = MagicMock()
    fake = MagicMock()
    fake.session_id = "sess-1"
    fake.user_id = "user-1"
    fake.last_active_at = _dt.datetime(2026, 1, 1,
                                        tzinfo=_dt.timezone.utc)
    blueprint_app.teams_link_service.repo.find_conversation_session\
        .return_value = fake
    rv = client.get(
        "/api/auth/teams-bridge/conversation",
        query_string={"conversation_id": "conv-1"},
        headers={"X-Service-Key": os.environ["ASSETATLAS_BRIDGE_API_KEY"]},
    )
    assert rv.status_code == 200
    body = rv.get_json()["data"]
    assert body["session_id"] == "sess-1"
    assert body["user_id"] == "user-1"


def test_post_conversation_session_creates(client, blueprint_app):
    blueprint_app.teams_link_service = MagicMock()
    fake = MagicMock()
    fake.session_id = "sess-1"
    blueprint_app.teams_link_service.repo.create_conversation_session\
        .return_value = fake
    rv = client.post(
        "/api/auth/teams-bridge/conversation",
        json={"conversation_id": "conv-1",
              "user_id": "u-1", "session_id": "s-1"},
        headers={"X-Service-Key": os.environ["ASSETATLAS_BRIDGE_API_KEY"]},
    )
    assert rv.status_code == 200


def test_delete_conversation_session(client, blueprint_app):
    blueprint_app.teams_link_service = MagicMock()
    rv = client.delete(
        "/api/auth/teams-bridge/conversation",
        query_string={"conversation_id": "conv-1"},
        headers={"X-Service-Key": os.environ["ASSETATLAS_BRIDGE_API_KEY"]},
    )
    assert rv.status_code == 200
    blueprint_app.teams_link_service.repo.delete_conversation_session\
        .assert_called_once_with("conv-1")
