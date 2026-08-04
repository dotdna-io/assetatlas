# SPDX-License-Identifier: AGPL-3.0-or-later
"""TeamsLinkService unit tests — link-token mint, verify, single-use."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.modules.teams_link.service import (
    LinkTokenError, TeamsLinkService,
)


@pytest.fixture
def svc():
    repo = MagicMock()
    used_jti_store = MagicMock()
    used_jti_store.is_used.return_value = False
    used_jti_store.mark_used.return_value = True
    s = TeamsLinkService(
        repo=repo,
        used_jti_store=used_jti_store,
        secret_key="test-secret-32-bytes-long-padding-x",
        public_base_url="https://aa.example.com",
        token_ttl_seconds=300,
    )
    return s, repo, used_jti_store


def test_mint_link_token_produces_url(svc):
    s, _, _ = svc
    url, expires_at = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="Alice",
    )
    assert url.startswith("https://aa.example.com/teams/link?token=")
    assert isinstance(expires_at, dt.datetime)


def test_verify_link_token_returns_payload(svc):
    s, _, _ = svc
    url, _ = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="Alice",
    )
    token = url.rsplit("=", 1)[1]
    claims = s.verify_link_token(token)
    assert claims["aad_object_id"] == "aad-1"
    assert claims["tenant_id"] == "t-1"
    assert claims["display_name"] == "Alice"
    assert "jti" in claims


def test_verify_rejects_used_jti(svc):
    s, _, used = svc
    url, _ = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="Alice",
    )
    token = url.rsplit("=", 1)[1]
    used.is_used.return_value = True
    with pytest.raises(LinkTokenError):
        s.verify_link_token(token)


def test_verify_rejects_bad_signature(svc):
    s, _, _ = svc
    with pytest.raises(LinkTokenError):
        s.verify_link_token("not.a.real.jwt")


def test_consume_marks_jti_used(svc):
    s, _, used = svc
    url, _ = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="A",
    )
    token = url.rsplit("=", 1)[1]
    s.consume_link_token(token, user_id=uuid4(), client_ip="1.2.3.4")
    used.mark_used.assert_called_once()


def test_consume_race_loss_raises(svc):
    s, _, used = svc
    url, _ = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="A",
    )
    token = url.rsplit("=", 1)[1]
    used.mark_used.return_value = False
    with pytest.raises(LinkTokenError):
        s.consume_link_token(token, user_id=uuid4(), client_ip="1.2.3.4")


def test_init_rejects_short_secret():
    with pytest.raises(ValueError, match="at least 32 characters"):
        TeamsLinkService(
            repo=MagicMock(),
            used_jti_store=MagicMock(),
            secret_key="too-short",
            public_base_url="https://x",
        )


def test_init_rejects_empty_secret():
    with pytest.raises(ValueError):
        TeamsLinkService(
            repo=MagicMock(),
            used_jti_store=MagicMock(),
            secret_key="",
            public_base_url="https://x",
        )


def test_find_link_delegates_to_repo(svc):
    s, repo, _ = svc
    repo.find_link.return_value = "sentinel"
    assert s.find_link("aad-x", "t-x") == "sentinel"
    repo.find_link.assert_called_once_with("aad-x", "t-x")


def test_callable_url_provider_resolves_per_mint():
    """Mint must re-read the URL each call, so settings changes propagate
    without restarting the API container."""
    used = MagicMock()
    used.is_used.return_value = False
    used.mark_used.return_value = True

    current = ['https://first.example.com']

    s = TeamsLinkService(
        repo=MagicMock(),
        used_jti_store=used,
        secret_key="test-secret-32-bytes-long-padding-x",
        public_base_url=lambda: current[0],
    )

    url1, _ = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="A",
    )
    assert url1.startswith("https://first.example.com/teams/link?")

    # Operator updates the setting; next mint picks up the new value.
    current[0] = "https://second.example.com"
    url2, _ = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="A",
    )
    assert url2.startswith("https://second.example.com/teams/link?")


def test_callable_url_provider_strips_trailing_slash():
    used = MagicMock()
    used.is_used.return_value = False
    s = TeamsLinkService(
        repo=MagicMock(),
        used_jti_store=used,
        secret_key="test-secret-32-bytes-long-padding-x",
        public_base_url=lambda: "https://x.example.com/",
    )
    url, _ = s.mint_link_token(
        aad_object_id="aad-1", tenant_id="t-1", display_name="A",
    )
    assert url.startswith("https://x.example.com/teams/link?")
    assert "https://x.example.com//teams/link" not in url
