# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the chat blueprint (sessions CRUD + feedback)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.blueprints.chat.models import DEFAULT_SESSION_TITLE
from src.api.blueprints.chat.repository import (
    MessageRow, SessionRow, ToolCallRow,
)
from src.api.blueprints.chat.routes import CHAT_NOT_CONFIGURED


def _make_session_row(sid="s-1", uid="u-1", title="My chat",
                      model="claude", provider="anthropic"):
    now = datetime.now(timezone.utc)
    return SessionRow(id=sid, user_id=uid, title=title, model=model,
                      provider=provider, created_at=now, updated_at=now)


def _make_message_row(mid="m-1", seq=1, role="user", content="hi"):
    now = datetime.now(timezone.utc)
    return MessageRow(id=mid, session_id="s-1", seq=seq, role=role,
                      content=content, tool_calls=None,
                      input_tokens=None, output_tokens=None, error=None,
                      created_at=now)


def _fake_ping_client(captured, *, text="OK", stop_reason="end_turn",
                      provider="aws_bedrock",
                      model="mistral.mistral-7b-instruct-v0:2"):
    """Build a fake LLMClient whose ``.stream`` records the ``tools`` it was
    called with into ``captured`` and yields no chunks, exposing a
    StreamResult with the given text/stop_reason. Lets the config-test ping
    assertions inspect exactly what reaches the provider."""
    from src.api.blueprints.chat.llm.base import StreamResult, Usage

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def _aiter(self):
            return
            yield  # pragma: no cover - presence makes this an async generator

        def __aiter__(self):
            return self._aiter()

        @property
        def result(self):
            return StreamResult(stop_reason=stop_reason, final_text=text,
                                usage=Usage(input_tokens=2, output_tokens=3))

    class _Client:
        def __init__(self):
            self.provider = provider
            self.model = model

        async def stream(self, *, system_prompt, messages, tools,
                         max_output_tokens):
            captured["tools"] = tools
            return _Stream()

    return _Client()


# ---- Auth gate ----

class TestChatAuth:
    @pytest.mark.parametrize("method,path", [
        ("POST",   "/api/chat/sessions"),
        ("GET",    "/api/chat/sessions"),
        ("GET",    "/api/chat/sessions/abc"),
        ("PATCH",  "/api/chat/sessions/abc"),
        ("POST",   "/api/chat/sessions/abc/autotitle"),
        ("DELETE", "/api/chat/sessions/abc"),
        ("POST",   "/api/chat/feedback"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        kwargs = {"json": {}} if method in ("POST", "PATCH") else {}
        resp = getattr(client, method.lower())(path, **kwargs)
        assert resp.status_code == 401


# ---- POST /api/chat/sessions ----

class TestCreateSession:
    def test_creates_and_returns_session(self, client, authed_headers,
                                         blueprint_app):
        s = _make_session_row(sid="abc", title="My chat")
        blueprint_app.chat_service.create_session.return_value = s

        resp = client.post(
            "/api/chat/sessions",
            json={"title": "My chat"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["session_id"] == "abc"
        assert body["data"]["title"] == "My chat"
        blueprint_app.chat_service.create_session.assert_called_once()

    def test_uses_default_title_when_omitted(self, client, authed_headers,
                                             blueprint_app):
        blueprint_app.chat_service.create_session.return_value = (
            _make_session_row(title="Untitled conversation"))

        resp = client.post(
            "/api/chat/sessions",
            json={},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200


# ---- GET /api/chat/sessions ----

class TestListSessions:
    def test_returns_list_with_pagination(self, client, authed_headers,
                                          blueprint_app):
        out = MagicMock()
        out.sessions = [_make_session_row(sid="a"),
                        _make_session_row(sid="b")]
        out.total = 2
        blueprint_app.chat_service.repo.list_sessions.return_value = out

        resp = client.get(
            "/api/chat/sessions?page=1&per_page=10",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["total"] == 2
        assert body["page"] == 1
        assert body["per_page"] == 10
        assert len(body["sessions"]) == 2

    def test_per_page_clamped_to_100(self, client, authed_headers,
                                     blueprint_app):
        out = MagicMock(sessions=[], total=0)
        blueprint_app.chat_service.repo.list_sessions.return_value = out
        resp = client.get(
            "/api/chat/sessions?per_page=999",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["per_page"] == 100

    def test_page_clamped_to_1(self, client, authed_headers, blueprint_app):
        out = MagicMock(sessions=[], total=0)
        blueprint_app.chat_service.repo.list_sessions.return_value = out
        resp = client.get(
            "/api/chat/sessions?page=0",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["page"] == 1


# ---- GET /api/chat/sessions/<id> ----

class TestGetSession:
    def test_returns_session_with_messages(self, client, authed_headers,
                                           blueprint_app):
        s = _make_session_row(sid="abc")
        blueprint_app.chat_service.repo.load_session.return_value = s
        blueprint_app.chat_service.repo.load_messages.return_value = [
            _make_message_row(mid="m1", role="user", content="hi"),
            _make_message_row(mid="m2", role="assistant", content="hello"),
        ]

        resp = client.get(
            "/api/chat/sessions/abc",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["session"]["id"] == "abc"
        assert len(body["messages"]) == 2

    def test_404_when_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.chat_service.repo.load_session.return_value = None

        resp = client.get(
            "/api/chat/sessions/missing",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 404

    def test_merges_artifacts_into_tool_calls_on_history_load(
        self, client, authed_headers, blueprint_app,
    ):
        """Reopening a past conversation should re-render charts without
        an extra request — the artifact from chat_tool_calls is folded
        into each message.tool_calls entry (matched positionally)."""
        s = _make_session_row(sid="abc")
        artifact = {
            "type": "donut", "title": "Assets by Category",
            "series": [{"label": "server", "value": 5}],
            "meta": {"total": 5, "other_count": 0},
        }
        # Assistant message records its tool_use blocks in message.tool_calls.
        asst = MessageRow(
            id="m2", session_id="abc", seq=2, role="assistant",
            content="Here:", tool_calls=[
                {"id": "t1", "name": "aggregate_assets",
                 "input": {"row_field": "category"}},
            ],
            input_tokens=None, output_tokens=None, error=None,
            created_at=datetime.now(timezone.utc),
        )
        tool_row = ToolCallRow(
            id="tc1", message_id="m2", tool_name="aggregate_assets",
            arguments={"row_field": "category"},
            result="COUNT: 5", status="ok", duration_ms=20, error=None,
            created_at=datetime.now(timezone.utc),
            artifact=artifact,
        )

        blueprint_app.chat_service.repo.load_session.return_value = s
        blueprint_app.chat_service.repo.load_messages.return_value = [asst]
        blueprint_app.chat_service.repo.load_tool_calls_by_message \
            .return_value = {"m2": [tool_row]}

        resp = client.get(
            "/api/chat/sessions/abc",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        [asst_out] = body["messages"]
        assert asst_out["tool_calls"][0]["artifact"] == artifact

    def test_tool_calls_without_artifact_unchanged(
        self, client, authed_headers, blueprint_app,
    ):
        """Text-only tools leave their tool_calls entry untouched — no
        ``artifact`` key appears. This protects existing tool chips
        from a regression where every entry suddenly carries the field."""
        s = _make_session_row(sid="abc")
        asst = MessageRow(
            id="m2", session_id="abc", seq=2, role="assistant",
            content="ok", tool_calls=[
                {"id": "t1", "name": "search_assets",
                 "input": {"query": "host"}},
            ],
            input_tokens=None, output_tokens=None, error=None,
            created_at=datetime.now(timezone.utc),
        )
        tool_row = ToolCallRow(
            id="tc1", message_id="m2", tool_name="search_assets",
            arguments={"query": "host"},
            result="found", status="ok", duration_ms=5, error=None,
            created_at=datetime.now(timezone.utc),
            artifact=None,
        )

        blueprint_app.chat_service.repo.load_session.return_value = s
        blueprint_app.chat_service.repo.load_messages.return_value = [asst]
        blueprint_app.chat_service.repo.load_tool_calls_by_message \
            .return_value = {"m2": [tool_row]}

        resp = client.get(
            "/api/chat/sessions/abc",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        [asst_out] = resp.get_json()["data"]["messages"]
        assert "artifact" not in asst_out["tool_calls"][0]


# ---- PATCH /api/chat/sessions/<id> ----

class TestRenameSession:
    def test_renames_and_returns(self, client, authed_headers, blueprint_app):
        s = _make_session_row(sid="abc", title="Renamed")
        blueprint_app.chat_service.repo.rename_session.return_value = s

        resp = client.patch(
            "/api/chat/sessions/abc",
            json={"title": "Renamed"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["title"] == "Renamed"

    def test_404_when_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.chat_service.repo.rename_session.return_value = None

        resp = client.patch(
            "/api/chat/sessions/missing",
            json={"title": "x"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 404


# ---- POST /api/chat/sessions/<id>/autotitle ----

class TestAutoTitleSession:
    def test_overwrites_default_title(self, client, authed_headers,
                                      blueprint_app):
        svc = blueprint_app.chat_service
        before = _make_session_row(sid="abc", title=DEFAULT_SESSION_TITLE)
        after = _make_session_row(sid="abc", title="Workstation Compliance")
        svc.repo.load_session.return_value = before
        svc.repo.rename_session.return_value = after
        svc.generate_title = AsyncMock(return_value="Workstation Compliance")

        resp = client.post(
            "/api/chat/sessions/abc/autotitle",
            json={"user_text": "how many workstations are out of date?"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["title"] == "Workstation Compliance"
        svc.generate_title.assert_awaited_once()
        svc.repo.rename_session.assert_called_once()

    def test_preserves_user_set_title(self, client, authed_headers,
                                      blueprint_app):
        """If the session already has a non-default title (user renamed it),
        the LLM must not be called and the title stays put."""
        svc = blueprint_app.chat_service
        existing = _make_session_row(sid="abc", title="My Custom Title")
        svc.repo.load_session.return_value = existing
        svc.generate_title = AsyncMock(return_value="Should Not Run")

        resp = client.post(
            "/api/chat/sessions/abc/autotitle",
            json={"user_text": "anything"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["title"] == "My Custom Title"
        svc.generate_title.assert_not_awaited()
        svc.repo.rename_session.assert_not_called()

    def test_falls_back_to_derived_title_when_llm_returns_none(
            self, client, authed_headers, blueprint_app):
        """Empty LLM output: apply a title derived from the opening message so
        the session is never left on the default — a reasoning model that
        times out (and so produces nothing) must still get a usable title."""
        svc = blueprint_app.chat_service
        before = _make_session_row(sid="abc", title=DEFAULT_SESSION_TITLE)
        after = _make_session_row(sid="abc", title="Hello There")
        svc.repo.load_session.return_value = before
        svc.repo.rename_session.return_value = after
        svc.generate_title = AsyncMock(return_value=None)
        svc.fallback_title.return_value = "Hello There"

        resp = client.post(
            "/api/chat/sessions/abc/autotitle",
            json={"user_text": "hello there"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["title"] == "Hello There"
        svc.fallback_title.assert_called_once_with("hello there")
        svc.repo.rename_session.assert_called_once()

    def test_falls_back_to_derived_title_when_llm_raises(
            self, client, authed_headers, blueprint_app):
        """Provider error or wall-clock timeout is caught in the route; it then
        applies a derived title so the session is never left untitled."""
        svc = blueprint_app.chat_service
        before = _make_session_row(sid="abc", title=DEFAULT_SESSION_TITLE)
        after = _make_session_row(sid="abc", title="Hello There")
        svc.repo.load_session.return_value = before
        svc.repo.rename_session.return_value = after
        svc.generate_title = AsyncMock(side_effect=RuntimeError("provider"))
        svc.fallback_title.return_value = "Hello There"

        resp = client.post(
            "/api/chat/sessions/abc/autotitle",
            json={"user_text": "hello there"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["title"] == "Hello There"
        svc.fallback_title.assert_called_once_with("hello there")
        svc.repo.rename_session.assert_called_once()

    def test_keeps_default_when_llm_and_fallback_both_empty(
            self, client, authed_headers, blueprint_app):
        """If the LLM produces nothing AND the derived fallback is also empty
        (e.g. an effectively-blank message), return the unchanged session
        rather than renaming to an empty title."""
        svc = blueprint_app.chat_service
        before = _make_session_row(sid="abc", title=DEFAULT_SESSION_TITLE)
        svc.repo.load_session.return_value = before
        svc.generate_title = AsyncMock(return_value=None)
        svc.fallback_title.return_value = None

        resp = client.post(
            "/api/chat/sessions/abc/autotitle",
            json={"user_text": "hi"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["title"] == DEFAULT_SESSION_TITLE
        svc.repo.rename_session.assert_not_called()

    def test_404_when_session_missing(self, client, authed_headers,
                                      blueprint_app):
        blueprint_app.chat_service.repo.load_session.return_value = None

        resp = client.post(
            "/api/chat/sessions/missing/autotitle",
            json={"user_text": "hello"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 404


# ---- DELETE /api/chat/sessions/<id> ----

class TestDeleteSession:
    def test_deletes(self, client, authed_headers, blueprint_app):
        blueprint_app.chat_service.repo.delete_session.return_value = True

        resp = client.delete(
            "/api/chat/sessions/abc",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200

    def test_404_when_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.chat_service.repo.delete_session.return_value = False

        resp = client.delete(
            "/api/chat/sessions/missing",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 404


# ---- POST /api/chat/feedback ----

class TestFeedback:
    def test_accepts_thumbs_up(self, client, authed_headers, blueprint_app):
        resp = client.post(
            "/api/chat/feedback",
            json={"message_id": "m-1", "rating": 1, "comment": "good"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.chat_service.repo.upsert_feedback.assert_called_once()

    def test_rejects_invalid_rating(self, client, authed_headers,
                                    blueprint_app):
        resp = client.post(
            "/api/chat/feedback",
            json={"message_id": "m-1", "rating": 5},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400


# ---- GET /api/chat/usage ----

class TestUsage:
    def test_returns_count_remaining_limit(self, client, authed_headers,
                                           blueprint_app):
        blueprint_app.chat_rate_limiter.current.return_value = (3, 197)
        blueprint_app.chat_rate_limiter.per_day = 200
        resp = client.get(
            "/api/chat/usage",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["messages_today"] == 3
        assert data["remaining"] == 197
        assert data["limit"] == 200

    def test_no_auth_returns_401(self, client):
        resp = client.get("/api/chat/usage")
        assert resp.status_code == 401


# ---- GET /api/chat/admin/usage ----

class TestAdminUsage:
    def test_returns_aggregated_stats(self, client, authed_headers,
                                      blueprint_app):
        blueprint_app.chat_service.repo.usage_summary.return_value = {
            "window_days": 7,
            "totals": {"messages": 12, "sessions": 3, "tokens_input": 100,
                       "tokens_output": 20, "active_users": 2},
            "top_users": [],
            "by_provider": [],
            "tool_calls": [],
            "recent_downvotes": [],
        }
        resp = client.get(
            "/api/chat/admin/usage?days=7",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["window_days"] == 7
        blueprint_app.chat_service.repo.usage_summary.assert_called_once_with(
            days=7)

    def test_clamps_days_to_90(self, client, authed_headers, blueprint_app):
        blueprint_app.chat_service.repo.usage_summary.return_value = {
            "window_days": 90, "totals": {}, "top_users": [],
            "by_provider": [], "tool_calls": [], "recent_downvotes": [],
        }
        client.get("/api/chat/admin/usage?days=9999",
                   headers=authed_headers("admin"))
        blueprint_app.chat_service.repo.usage_summary.assert_called_with(days=90)

    def test_user_role_forbidden(self, client, authed_headers):
        resp = client.get("/api/chat/admin/usage",
                          headers=authed_headers("user"))
        assert resp.status_code == 403

    def test_no_auth_returns_401(self, client):
        assert client.get("/api/chat/admin/usage").status_code == 401


# ---- GET /api/chat/admin/audit ----

class TestAdminAudit:
    def test_returns_paginated_items(self, client, authed_headers,
                                     blueprint_app):
        blueprint_app.chat_service.repo.audit_messages.return_value = {
            "items": [], "total": 0, "page": 1, "per_page": 50,
        }
        resp = client.get("/api/chat/admin/audit",
                          headers=authed_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["page"] == 1
        assert body["per_page"] == 50
        blueprint_app.chat_service.repo.audit_messages.assert_called_once_with(
            days=7, page=1, per_page=50, user_id=None, role=None)

    def test_forwards_filters(self, client, authed_headers, blueprint_app):
        blueprint_app.chat_service.repo.audit_messages.return_value = {
            "items": [], "total": 0, "page": 2, "per_page": 25,
        }
        client.get(
            "/api/chat/admin/audit?days=14&page=2&per_page=25"
            "&user_id=u-42&role=user",
            headers=authed_headers("admin"),
        )
        blueprint_app.chat_service.repo.audit_messages.assert_called_with(
            days=14, page=2, per_page=25, user_id="u-42", role="user")

    def test_user_role_forbidden(self, client, authed_headers):
        resp = client.get("/api/chat/admin/audit",
                          headers=authed_headers("user"))
        assert resp.status_code == 403


class TestAdminAuditRoleValidation:
    def test_bogus_role_returns_400(self, client, authed_headers,
                                    blueprint_app):
        resp = client.get("/api/chat/admin/audit?role=spam",
                          headers=authed_headers("admin"))
        assert resp.status_code == 400
        # Repo must not be called when the validator short-circuits.
        blueprint_app.chat_service.repo.audit_messages.assert_not_called()

    def test_valid_role_passes_through(self, client, authed_headers,
                                       blueprint_app):
        blueprint_app.chat_service.repo.audit_messages.return_value = {
            "items": [], "total": 0, "page": 1, "per_page": 50,
        }
        resp = client.get("/api/chat/admin/audit?role=assistant",
                          headers=authed_headers("admin"))
        assert resp.status_code == 200
        blueprint_app.chat_service.repo.audit_messages.assert_called_with(
            days=7, page=1, per_page=50, user_id=None, role="assistant")


# ---- GET/PATCH /api/chat/admin/config + /admin/config/test ----

class TestAdminChatConfig:
    """Per-provider config (issue #636).

    Wires ``chat_providers_service`` MagicMock to return realistic shapes,
    then asserts the routes call the service correctly and surface its
    output. The service itself has dedicated unit tests; these focus on
    blueprint contract: validation, status codes, rebuild semantics."""

    def _stub_admin_config(self, blueprint_app, *, active="anthropic",
                            chat_enabled=True,
                            api_key_configured_for=("anthropic",)):
        from src.api.blueprints.chat.admin_config.models import (
            AdminChatConfig, ProviderConfig,
        )
        providers = {}
        for p in ("anthropic", "openai", "openai_compat"):
            configured = p in api_key_configured_for
            providers[p] = ProviderConfig(
                provider=p,
                model={
                    "anthropic": "claude-sonnet-4-6",
                    "openai": "gpt-4o-mini",
                    "openai_compat": "",
                }[p],
                api_key_preview="••••1234" if configured else "",
                api_key_configured=configured,
                base_url="",
                prompt_cache=True,
                thinking_enabled=True,
            )
        cfg = AdminChatConfig(
            active_provider=active, chat_enabled=chat_enabled,
            providers=providers,
        )
        blueprint_app.chat_providers_service.get_admin_config.return_value = cfg
        blueprint_app.chat_providers_service.get_active_provider.return_value = (
            active
        )
        # The Bedrock /test path merges this overlay into the effective config;
        # default it to a no-op so non-Bedrock tests aren't affected.
        blueprint_app.chat_providers_service.bedrock_test_overlay.return_value = {}
        return cfg

    # ------------------------------------------------------------------
    # GET /api/chat/admin/config
    # ------------------------------------------------------------------

    def test_get_returns_all_providers_and_active(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app, active="openai",
                                api_key_configured_for=("anthropic", "openai"))
        resp = client.get("/api/chat/admin/config",
                          headers=authed_headers("admin"))
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["active_provider"] == "openai"
        assert set(data["providers"].keys()) == {
            "anthropic", "openai", "openai_compat"
        }

    def test_get_masks_api_keys(self, client, authed_headers, blueprint_app):
        self._stub_admin_config(blueprint_app,
                                api_key_configured_for=("anthropic",))
        resp = client.get("/api/chat/admin/config",
                          headers=authed_headers("admin"))
        data = resp.get_json()["data"]
        ant = data["providers"]["anthropic"]
        assert ant["api_key_configured"] is True
        assert ant["api_key_preview"].startswith("•")
        op = data["providers"]["openai"]
        assert op["api_key_configured"] is False
        assert op["api_key_preview"] == ""

    def test_get_chat_enabled_passthrough(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app, chat_enabled=False)
        resp = client.get("/api/chat/admin/config",
                          headers=authed_headers("admin"))
        assert resp.get_json()["data"]["chat_enabled"] is False

    # ------------------------------------------------------------------
    # PATCH /api/chat/admin/config
    # ------------------------------------------------------------------

    def test_patch_updates_one_provider_only(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app, active="anthropic",
                                api_key_configured_for=("anthropic",))
        blueprint_app.chat_providers_service.update_provider = MagicMock()
        blueprint_app.chat_providers_service.set_active_provider = MagicMock()

        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"providers": {
                "openai": {"model": "gpt-4o", "api_key": "sk-new"},
            }},
        )
        assert resp.status_code == 200
        blueprint_app.chat_providers_service.update_provider.assert_called_once()
        call = blueprint_app.chat_providers_service.update_provider.call_args
        assert call.args[0] == "openai"
        # Inactive provider's update doesn't toggle active.
        blueprint_app.chat_providers_service.set_active_provider \
            .assert_not_called()

    def test_patch_active_provider_only(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app, active="anthropic",
                                api_key_configured_for=("anthropic", "openai"))
        blueprint_app.chat_providers_service.update_provider = MagicMock()
        blueprint_app.chat_providers_service.set_active_provider = MagicMock()
        # The post-PATCH rebuild path needs an effective config.
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "openai",
                "CHAT_LLM_MODEL": "gpt-4o-mini",
                "CHAT_LLM_API_KEY": "sk-x",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }

        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"active_provider": "openai"},
        )
        assert resp.status_code == 200
        blueprint_app.chat_providers_service.set_active_provider \
            .assert_called_once()

    def test_patch_active_provider_change_rebuilds_llm(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        self._stub_admin_config(blueprint_app, active="anthropic",
                                api_key_configured_for=("anthropic", "openai"))
        blueprint_app.chat_providers_service.set_active_provider = MagicMock()
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "openai",
                "CHAT_LLM_MODEL": "gpt-4o-mini",
                "CHAT_LLM_API_KEY": "sk-x",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }
        rebuild_spy = MagicMock()
        blueprint_app.chat_service.rebuild_llm = rebuild_spy
        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client",
            MagicMock(return_value=MagicMock(provider="openai",
                                              model="gpt-4o-mini")),
        )

        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"active_provider": "openai"},
        )
        assert resp.status_code == 200
        rebuild_spy.assert_called_once()

    def test_patch_inactive_provider_does_not_rebuild(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        self._stub_admin_config(blueprint_app, active="anthropic",
                                api_key_configured_for=("anthropic",))
        blueprint_app.chat_providers_service.update_provider = MagicMock()
        rebuild_spy = MagicMock()
        blueprint_app.chat_service.rebuild_llm = rebuild_spy
        # Even if a make_llm_client mock is sitting there, the active
        # provider didn't change AND the active provider's config wasn't
        # touched, so rebuild must not run.
        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client",
            MagicMock(return_value=MagicMock()),
        )

        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"providers": {"openai": {"model": "gpt-4o"}}},
        )
        assert resp.status_code == 200
        rebuild_spy.assert_not_called()

    def test_patch_active_provider_null_deactivates(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        # Explicit null disables all providers: clear the active setting and
        # DON'T rebuild a client (there's nothing to build). #912.
        self._stub_admin_config(blueprint_app, active="anthropic",
                                api_key_configured_for=("anthropic",))
        blueprint_app.chat_providers_service.set_active_provider = MagicMock()
        make = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client", make,
        )
        rebuild_spy = MagicMock()
        blueprint_app.chat_service.rebuild_llm = rebuild_spy

        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"active_provider": None},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        blueprint_app.chat_providers_service.set_active_provider \
            .assert_called_once()
        assert blueprint_app.chat_providers_service.set_active_provider \
            .call_args.args[0] is None
        make.assert_not_called()
        rebuild_spy.assert_not_called()

    def test_patch_active_provider_field_change_rebuilds(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        self._stub_admin_config(blueprint_app, active="anthropic",
                                api_key_configured_for=("anthropic",))
        blueprint_app.chat_providers_service.update_provider = MagicMock()
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "anthropic",
                "CHAT_LLM_MODEL": "claude-opus-4-7",
                "CHAT_LLM_API_KEY": "sk-x",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }
        rebuild_spy = MagicMock()
        blueprint_app.chat_service.rebuild_llm = rebuild_spy
        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client",
            MagicMock(return_value=MagicMock(provider="anthropic",
                                              model="claude-opus-4-7")),
        )

        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"providers": {"anthropic": {"model": "claude-opus-4-7"}}},
        )
        assert resp.status_code == 200
        rebuild_spy.assert_called_once()

    def test_patch_invalid_provider_in_body_400(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app)
        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"providers": {"bedrock": {"model": "claude"}}},
        )
        assert resp.status_code == 400

    def test_patch_invalid_active_provider_400(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app)
        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"active_provider": "acme"},
        )
        assert resp.status_code == 400

    def test_patch_bool_field_type_validation(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app)
        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"providers": {"anthropic": {"prompt_cache": "yes"}}},
        )
        assert resp.status_code == 400

    def test_patch_rollback_keeps_old_active_when_rebuild_fails(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        """When the rebuild for a newly-activated provider fails, the
        active-provider toggle MUST NOT persist — operators shouldn't end
        up pointing at a broken provider. The route surfaces the build
        error as 400."""
        self._stub_admin_config(blueprint_app, active="anthropic",
                                api_key_configured_for=("anthropic",))
        set_active_spy = MagicMock()
        blueprint_app.chat_providers_service.set_active_provider = set_active_spy
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "openai",
                "CHAT_LLM_MODEL": "gpt-4o-mini",
                "CHAT_LLM_API_KEY": "",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }
        rebuild_spy = MagicMock()
        blueprint_app.chat_service.rebuild_llm = rebuild_spy
        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client",
            MagicMock(side_effect=ValueError("no api key")),
        )

        resp = client.patch(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
            json={"active_provider": "openai"},
        )
        assert resp.status_code == 400
        rebuild_spy.assert_not_called()
        set_active_spy.assert_not_called()

    def test_user_role_forbidden_on_admin_routes(self, client, authed_headers):
        assert client.get("/api/chat/admin/config",
                          headers=authed_headers("user")).status_code == 403
        assert client.patch("/api/chat/admin/config",
                            headers=authed_headers("user"),
                            json={}).status_code == 403
        assert client.post("/api/chat/admin/config/test",
                           headers=authed_headers("user"),
                           json={"provider": "anthropic"}).status_code == 403

    # ------------------------------------------------------------------
    # POST /api/chat/admin/config/test
    # ------------------------------------------------------------------

    def test_test_endpoint_requires_provider(
        self, client, authed_headers, blueprint_app,
    ):
        self._stub_admin_config(blueprint_app)
        resp = client.post(
            "/api/chat/admin/config/test",
            headers=authed_headers("admin"),
            json={},
        )
        assert resp.status_code == 400

    def test_test_endpoint_overlays_fields_on_effective_config(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        self._stub_admin_config(blueprint_app)
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "openai_compat",
                "CHAT_LLM_MODEL": "qwen3",
                "CHAT_LLM_API_KEY": "",
                "CHAT_LLM_BASE_URL": "http://vllm:8000",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }
        captured: dict = {}

        def _fake_factory(cfg):
            captured["cfg"] = dict(cfg)
            raise ValueError("expected — short-circuiting before async ping")

        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client", _fake_factory,
        )
        resp = client.post(
            "/api/chat/admin/config/test",
            headers=authed_headers("admin"),
            json={"provider": "openai_compat",
                  "base_url": "http://other:8001"},
        )
        # Build error returns ok=False, not a 500.
        assert resp.status_code == 200
        assert resp.get_json()["data"]["ok"] is False
        # The overlay applied — base_url replaced.
        assert captured["cfg"]["CHAT_LLM_BASE_URL"] == "http://other:8001"

    def test_test_endpoint_reports_build_error_as_ok_false(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        self._stub_admin_config(blueprint_app)
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "openai_compat",
                "CHAT_LLM_MODEL": "qwen3",
                "CHAT_LLM_API_KEY": "",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }
        resp = client.post(
            "/api/chat/admin/config/test",
            headers=authed_headers("admin"),
            json={"provider": "openai_compat"},
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["ok"] is False
        assert data["provider"] == "openai_compat"

    def test_test_endpoint_ping_exercises_tool_use(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        """The Test ping must send a tool definition so it exercises the same
        Converse tool-use capability every real chat turn requires. Pinging
        with ``tools=[]`` greenlights tool-incapable models that then fail on
        the first real message (issue #695, Problem 1)."""
        self._stub_admin_config(blueprint_app)
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "aws_bedrock",
                "CHAT_LLM_MODEL": "mistral.mistral-7b-instruct-v0:2",
                "CHAT_LLM_API_KEY": "",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }
        captured: dict = {}
        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client",
            lambda cfg: _fake_ping_client(captured),
        )
        resp = client.post(
            "/api/chat/admin/config/test",
            headers=authed_headers("admin"),
            json={"provider": "aws_bedrock"},
        )
        assert resp.status_code == 200
        # A non-empty ToolSpec list reached the client — the ping exercises
        # tool use the same way production turns do.
        from src.api.blueprints.chat.llm.base import ToolSpec
        assert captured.get("tools"), "ping must send at least one tool"
        assert all(isinstance(t, ToolSpec) for t in captured["tools"])

    def test_test_endpoint_merges_bedrock_draft_credentials(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        """The Bedrock Test path must overlay the draft region + auth fields
        onto the effective config so make_llm_client sees the credentials on
        the form, not just the saved ones. Regression: overlay_map dropped
        every AWS field, so testing an unsaved Bedrock config validated
        nothing the operator typed."""
        self._stub_admin_config(blueprint_app)
        # First-time setup: no saved effective config yet.
        blueprint_app.chat_providers_service.get_effective_config.side_effect = (
            ValueError("no row")
        )
        blueprint_app.chat_providers_service.bedrock_test_overlay.return_value = {
            "CHAT_LLM_AWS_REGION": "us-west-2",
            "CHAT_LLM_AWS_AUTH": {
                "auth_mode": "access_keys",
                "access_key_id": "AKIA_DRAFT",
                "secret_access_key": "s",
            },
        }
        captured: dict = {}

        def _capture(cfg):
            captured["cfg"] = cfg
            return _fake_ping_client({})

        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client", _capture,
        )
        resp = client.post(
            "/api/chat/admin/config/test",
            headers=authed_headers("admin"),
            json={
                "provider": "aws_bedrock",
                "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "aws_region": "us-west-2",
                "auth_mode": "access_keys",
                "access_key_id": "AKIA_DRAFT",
                "secret_access_key": "s",
            },
        )
        assert resp.status_code == 200
        # The draft credentials reached client construction.
        assert captured["cfg"]["CHAT_LLM_AWS_REGION"] == "us-west-2"
        assert captured["cfg"]["CHAT_LLM_AWS_AUTH"]["access_key_id"] == (
            "AKIA_DRAFT"
        )
        # And the route built the draft from the posted AWS fields.
        call = (
            blueprint_app.chat_providers_service.bedrock_test_overlay.call_args
        )
        draft = call.args[0]
        assert draft.aws_region == "us-west-2"
        assert draft.access_key_id == "AKIA_DRAFT"

    def test_test_endpoint_ok_when_model_invokes_probe_tool(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        """A tool-capable model may answer the ping by invoking the probe tool
        instead of replying with text. That still proves tool-use support, so
        ``ok`` must be True even though final text is empty (issue #695)."""
        self._stub_admin_config(blueprint_app)
        blueprint_app.chat_providers_service.get_effective_config \
            .return_value = {
                "CHAT_LLM_PROVIDER": "aws_bedrock",
                "CHAT_LLM_MODEL": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "CHAT_LLM_API_KEY": "",
                "CHAT_LLM_BASE_URL": "",
                "CHAT_PROMPT_CACHE": True,
                "CHAT_LLM_THINKING_ENABLED": True,
            }
        captured: dict = {}
        monkeypatch.setattr(
            "src.api.blueprints.chat.llm.make_llm_client",
            lambda cfg: _fake_ping_client(captured, text="",
                                          stop_reason="tool_use"),
        )
        resp = client.post(
            "/api/chat/admin/config/test",
            headers=authed_headers("admin"),
            json={"provider": "aws_bedrock"},
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["ok"] is True
        assert data["stop_reason"] == "tool_use"


# ---------------------------------------------------------------------------
# @require_chat_service guard: every user-facing route 503s with the
# standard error envelope when chat_service is None, while admin/config
# stays reachable (uses chat_providers_service, not chat_service).
# Regression test for the chicken-and-egg pre-fix where the blueprint was
# only registered when chat_service init succeeded.
# ---------------------------------------------------------------------------

class TestChatServiceGuard:
    @pytest.fixture
    def chat_disabled(self, blueprint_app, monkeypatch):
        """Drop chat_service for the duration of one test.

        Uses monkeypatch so the original value is restored automatically
        even on test failure, and to make the intent (test-scoped override
        of a session-scoped fixture) explicit.
        """
        monkeypatch.setattr(blueprint_app, "chat_service", None, raising=False)
        return blueprint_app

    @pytest.mark.parametrize("method,path,body", [
        ("POST",   "/api/chat/sessions",                {"title": "x"}),
        ("GET",    "/api/chat/sessions",                None),
        ("GET",    "/api/chat/sessions/abc",            None),
        ("PATCH",  "/api/chat/sessions/abc",            {"title": "x"}),
        ("POST",   "/api/chat/sessions/abc/autotitle",  {"user_text": "x"}),
        ("DELETE", "/api/chat/sessions/abc",            None),
        ("POST",   "/api/chat/feedback",                {"message_id": "m1", "rating": 1}),
        ("GET",    "/api/chat/usage",                   None),
        ("GET",    "/api/chat/admin/usage",             None),
        ("GET",    "/api/chat/admin/audit",             None),
    ])
    def test_user_facing_route_503_when_chat_service_is_none(
        self, client, authed_headers, chat_disabled, method, path, body,
    ):
        role = "admin" if "/admin/" in path else "user"
        kwargs = {"json": body} if body is not None else {}
        resp = getattr(client, method.lower())(path, headers=authed_headers(role), **kwargs)
        assert resp.status_code == 503, (
            f"{method} {path} expected 503 when chat_service is None, "
            f"got {resp.status_code}: {resp.get_data(as_text=True)}"
        )
        body_json = resp.get_json()
        # Goes through @handle_errors → standard APIError envelope.
        assert body_json["error"] == "service_unavailable"
        assert "Settings → Chatbot" in body_json["message"]
        assert body_json["details"]["code"] == CHAT_NOT_CONFIGURED

    def test_user_route_503_when_no_active_provider(
        self, client, authed_headers, blueprint_app,
    ):
        # chat_service exists, but the admin disabled every provider
        # (get_active_provider → None): user-facing chat must 503, so no
        # worker keeps answering on a stale client. #912.
        blueprint_app.chat_providers_service.get_active_provider \
            .return_value = None
        resp = client.get(
            "/api/chat/sessions", headers=authed_headers("user"),
        )
        assert resp.status_code == 503, resp.get_data(as_text=True)
        assert resp.get_json()["details"]["code"] == CHAT_NOT_CONFIGURED

    def test_admin_config_get_reachable_when_chat_service_is_none(
        self, client, authed_headers, chat_disabled, blueprint_app,
    ):
        """The whole point of the chicken-and-egg fix: admins must be able to
        reach /admin/config to set up a provider even when chat_service is
        None at startup."""
        # admin_config uses chat_providers_service, not chat_service.
        cfg = MagicMock()
        cfg.model_dump.return_value = {"chat_enabled": False, "providers": {}}
        blueprint_app.chat_providers_service.get_admin_config.return_value = cfg

        resp = client.get(
            "/api/chat/admin/config",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_admin_config_routes_still_require_admin_role(
        self, client, authed_headers, chat_disabled,
    ):
        """Regression: removing the chat_service gate must not weaken the
        admin role check on /admin/config."""
        resp = client.get(
            "/api/chat/admin/config",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# #1124: a cold gunicorn worker must heal itself from the DB.
#
# The api runs four workers and each builds its own chat_service in
# create_app(). On a fresh install none of them can, so all four hold None;
# the admin's save then repairs only the worker that served that one PATCH.
# The other three kept 503ing until the container restarted, which made chat
# something you could switch off live but not on — and the 503's text sent the
# operator to check settings that were already correct.
#
# The DB is the only thing all four workers share, so the guard reuses the
# staleness key the warm path already rebuilds clients from. These pin both
# halves of that: it must heal when the DB says a provider exists, and it must
# not rebuild the tool registry on every request when one doesn't.
# ---------------------------------------------------------------------------

class TestColdWorkerHealsItself:
    @pytest.fixture
    def cold_worker(self, blueprint_app, monkeypatch):
        """A worker that booted before any provider existed.

        chat_service is None while the DB now holds a usable provider — the
        exact state three workers in four are left in after an admin saves.
        """
        monkeypatch.setattr(blueprint_app, "chat_service", None, raising=False)
        monkeypatch.setattr(blueprint_app, "chat_init_attempted_fingerprint",
                            None, raising=False)
        monkeypatch.setitem(blueprint_app.config, "CHAT_ENABLED", True)
        providers = blueprint_app.chat_providers_service
        monkeypatch.setattr(providers, "get_active_provider",
                            lambda: "anthropic")
        monkeypatch.setattr(
            providers, "get_active_config_fingerprint",
            lambda: ("anthropic", "2026-07-27T10:00:00"),
        )
        return blueprint_app

    @staticmethod
    def _healthy_chat_service():
        svc = MagicMock(name="chat_service")
        out = MagicMock()
        out.sessions = [_make_session_row(sid="a")]
        out.total = 1
        svc.repo.list_sessions.return_value = out
        return svc

    def _patch_init(self, monkeypatch, app, builds):
        """Stand in for factory.init_chat_service, counting calls.

        `builds` decides what each call produces, so a test can make the first
        attempt fail and a later one succeed.
        """
        calls = []

        def fake_init(target_app, should_log=True):
            calls.append(should_log)
            target_app.chat_service = builds(len(calls))

        monkeypatch.setattr("src.api.factory.init_chat_service", fake_init)
        monkeypatch.setattr("src.api.factory.init_chat_rate_limiter",
                            lambda target_app, should_log=True: None)
        return calls

    def test_cold_worker_serves_the_request_instead_of_503(
        self, client, authed_headers, cold_worker, monkeypatch,
    ):
        """The bug: this returned 503 on three workers in four."""
        calls = self._patch_init(
            monkeypatch, cold_worker, lambda _n: self._healthy_chat_service())

        resp = client.get("/api/chat/sessions", headers=authed_headers("user"))

        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert len(calls) == 1, "guard should build the service exactly once"
        assert cold_worker.chat_service is not None

    def test_unconfigured_install_still_503s(
        self, client, authed_headers, cold_worker, monkeypatch,
    ):
        """Healing must not paper over a genuinely unconfigured install.

        get_active_provider falls back to a default name rather than None when
        nothing is set up, so "a provider is named" is not proof one works —
        only the build attempt is.
        """
        self._patch_init(monkeypatch, cold_worker, lambda _n: None)

        resp = client.get("/api/chat/sessions", headers=authed_headers("user"))

        assert resp.status_code == 503, resp.get_data(as_text=True)
        assert resp.get_json()["details"]["code"] == CHAT_NOT_CONFIGURED

    def test_failed_build_is_not_retried_until_the_config_changes(
        self, client, authed_headers, cold_worker, monkeypatch,
    ):
        """Bounds the cost of the heal on an install with no key yet.

        init_chat_service builds the whole tool registry and, on a real fault,
        logs a traceback. Retrying that per request would turn an idle chat
        panel into a hot loop and re-teach operators to ignore ERROR (#1104).
        """
        calls = self._patch_init(monkeypatch, cold_worker, lambda _n: None)

        for _ in range(3):
            resp = client.get("/api/chat/sessions",
                              headers=authed_headers("user"))
            assert resp.status_code == 503

        assert len(calls) == 1, (
            f"expected one build attempt across three requests, got {len(calls)}"
        )

    def test_corrected_config_reaches_a_worker_that_already_failed(
        self, client, authed_headers, cold_worker, monkeypatch,
    ):
        """The other half: the bound must not become a new way to get stuck.

        Fixing an API key advances the active row's updated_at, so the
        fingerprint moves and every worker retries — without a restart, which
        is the whole point.
        """
        calls = self._patch_init(
            monkeypatch, cold_worker,
            lambda n: None if n == 1 else self._healthy_chat_service())

        first = client.get("/api/chat/sessions", headers=authed_headers("user"))
        assert first.status_code == 503

        # Admin corrects the key: same provider, later updated_at.
        monkeypatch.setattr(
            cold_worker.chat_providers_service, "get_active_config_fingerprint",
            lambda: ("anthropic", "2026-07-27T11:00:00"),
        )

        second = client.get("/api/chat/sessions", headers=authed_headers("user"))
        assert second.status_code == 200, second.get_data(as_text=True)
        assert len(calls) == 2

    def test_no_build_attempted_when_chat_is_disabled_by_config(
        self, client, authed_headers, cold_worker, monkeypatch,
    ):
        """CHAT_ENABLED=false is a deployment decision, not a cold worker.

        Without this check the guard would rebuild the registry on every chat
        request of every install that deliberately runs without chat.
        """
        monkeypatch.setitem(cold_worker.config, "CHAT_ENABLED", False)
        calls = self._patch_init(
            monkeypatch, cold_worker, lambda _n: self._healthy_chat_service())

        resp = client.get("/api/chat/sessions", headers=authed_headers("user"))

        assert resp.status_code == 503
        assert calls == [], "chat is off by config; nothing should be built"

    def test_healed_worker_still_503s_once_every_provider_is_disabled(
        self, client, authed_headers, cold_worker, monkeypatch,
    ):
        """Healing must not defeat the disable path it sits in front of."""
        self._patch_init(
            monkeypatch, cold_worker, lambda _n: self._healthy_chat_service())
        monkeypatch.setattr(cold_worker.chat_providers_service,
                            "get_active_provider", lambda: None)

        resp = client.get("/api/chat/sessions", headers=authed_headers("user"))

        assert resp.status_code == 503, resp.get_data(as_text=True)
        assert resp.get_json()["details"]["code"] == CHAT_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Bedrock list_foundation_models endpoint
# ---------------------------------------------------------------------------


class TestAdminBedrockModelsEndpoint:
    def test_returns_live_list_when_boto3_succeeds(
        self, client, authed_headers, monkeypatch, blueprint_app,
    ):
        from src.api.blueprints.chat import routes as chat_routes

        # Saved Bedrock creds so the endpoint can resolve effective config.
        blueprint_app.chat_providers_service.get_effective_config.return_value = {
            "CHAT_LLM_PROVIDER": "aws_bedrock",
            "CHAT_LLM_MODEL": "",
            "CHAT_LLM_API_KEY": "",
            "CHAT_LLM_BASE_URL": "",
            "CHAT_PROMPT_CACHE": True,
            "CHAT_LLM_THINKING_ENABLED": False,
            "CHAT_LLM_AWS_REGION": "us-east-1",
            "CHAT_LLM_AWS_AUTH": {"auth_mode": "access_keys",
                                  "access_key_id": "AKIA", "secret_access_key": "s"},
        }
        fake_bedrock = MagicMock(name="bedrock-control")
        fake_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [
                # On-demand text model — surfaces by its bare modelId.
                {"modelId": "mistral.mistral-large-2402-v1:0",
                 "modelName": "Mistral Large",
                 "providerName": "Mistral AI",
                 "modelLifecycle": {"status": "ACTIVE"},
                 "responseStreamingSupported": True,
                 "inferenceTypesSupported": ["ON_DEMAND"],
                 "outputModalities": ["TEXT"]},
                # Legacy on-demand model — must be filtered out.
                {"modelId": "anthropic.claude-3-haiku-20240307-v1:0",
                 "modelName": "Claude 3 Haiku",
                 "providerName": "Anthropic",
                 "modelLifecycle": {"status": "LEGACY"},
                 "responseStreamingSupported": True,
                 "inferenceTypesSupported": ["ON_DEMAND"],
                 "outputModalities": ["TEXT"]},
                # Non-streaming embedding model — must be filtered out.
                {"modelId": "amazon.titan-embed-text-v2:0",
                 "modelName": "Titan Text Embeddings v2",
                 "providerName": "Amazon",
                 "modelLifecycle": {"status": "ACTIVE"},
                 "responseStreamingSupported": False,
                 "inferenceTypesSupported": ["ON_DEMAND"],
                 "outputModalities": ["EMBEDDING"]},
            ],
        }
        fake_bedrock.list_inference_profiles.return_value = {
            "inferenceProfileSummaries": [],
        }
        monkeypatch.setattr(
            chat_routes, "_bedrock_control_client",
            lambda region, **_kw: fake_bedrock,
        )
        resp = client.get(
            "/api/chat/admin/bedrock/models?region=us-east-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()["data"]
        assert body["source"] == "live"
        assert body["models"] == [{
            "id": "mistral.mistral-large-2402-v1:0",
            "name": "Mistral Large",
            "provider": "Mistral AI",
        }]
        # The model list must NOT be scoped to on-demand only at the AWS layer
        # — that is exactly what hides inference-profile-only Claude models.
        _fm_kwargs = fake_bedrock.list_foundation_models.call_args.kwargs
        assert "byInferenceType" not in _fm_kwargs, _fm_kwargs

    def test_surfaces_inference_profile_models(
        self, client, authed_headers, monkeypatch, blueprint_app,
    ):
        """Regression for the reported bug: current Claude models on Bedrock
        are ``INFERENCE_PROFILE``-only, so a ``byInferenceType="ON_DEMAND"``
        query hid them and the dropdown showed only Mistral. The endpoint must
        surface system-defined inference profiles (whose ``inferenceProfileId``
        is itself a valid converse_stream modelId) so Anthropic models appear
        AND are invocable."""
        from src.api.blueprints.chat import routes as chat_routes

        blueprint_app.chat_providers_service.get_effective_config.return_value = {
            "CHAT_LLM_PROVIDER": "aws_bedrock", "CHAT_LLM_MODEL": "",
            "CHAT_LLM_API_KEY": "", "CHAT_LLM_BASE_URL": "",
            "CHAT_PROMPT_CACHE": True, "CHAT_LLM_THINKING_ENABLED": False,
            "CHAT_LLM_AWS_REGION": "eu-west-3",
            "CHAT_LLM_AWS_AUTH": {"auth_mode": "access_keys",
                                  "access_key_id": "AKIA", "secret_access_key": "s"},
        }
        fake_bedrock = MagicMock(name="bedrock-control")
        fake_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [
                # On-demand → bare id surfaces.
                {"modelId": "mistral.mistral-large-2402-v1:0",
                 "modelName": "Mistral Large", "providerName": "Mistral AI",
                 "modelLifecycle": {"status": "ACTIVE"},
                 "responseStreamingSupported": True,
                 "inferenceTypesSupported": ["ON_DEMAND"],
                 "outputModalities": ["TEXT"]},
                # INFERENCE_PROFILE-only → bare id is NOT invocable, so it must
                # NOT be offered directly; only its profile may surface it.
                {"modelId": "anthropic.claude-opus-4-8",
                 "modelName": "Claude Opus 4.8", "providerName": "Anthropic",
                 "modelLifecycle": {"status": "ACTIVE"},
                 "responseStreamingSupported": True,
                 "inferenceTypesSupported": ["INFERENCE_PROFILE"],
                 "outputModalities": ["TEXT"]},
                # Embedding model behind an embed profile — must be excluded.
                {"modelId": "cohere.embed-v4:0",
                 "modelName": "Cohere Embed v4", "providerName": "Cohere",
                 "modelLifecycle": {"status": "ACTIVE"},
                 "responseStreamingSupported": False,
                 "inferenceTypesSupported": ["INFERENCE_PROFILE"],
                 "outputModalities": ["EMBEDDING"]},
            ],
        }
        fake_bedrock.list_inference_profiles.return_value = {
            "inferenceProfileSummaries": [
                {"inferenceProfileId": "eu.anthropic.claude-opus-4-8",
                 "inferenceProfileName": "EU Anthropic Claude Opus 4.8",
                 "status": "ACTIVE",
                 "models": [{"modelArn": "arn:aws:bedrock:eu-west-3::"
                             "foundation-model/anthropic.claude-opus-4-8"}]},
                # Underlying model is an embedding model — must be excluded.
                {"inferenceProfileId": "eu.cohere.embed-v4:0",
                 "inferenceProfileName": "EU Cohere Embed v4",
                 "status": "ACTIVE",
                 "models": [{"modelArn": "arn:aws:bedrock:eu-west-3::"
                             "foundation-model/cohere.embed-v4:0"}]},
            ],
        }
        monkeypatch.setattr(
            chat_routes, "_bedrock_control_client",
            lambda region, **_kw: fake_bedrock,
        )
        resp = client.get(
            "/api/chat/admin/bedrock/models?region=eu-west-3",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()["data"]
        assert body["source"] == "live"
        ids = [m["id"] for m in body["models"]]
        # On-demand Mistral still surfaces by bare id.
        assert "mistral.mistral-large-2402-v1:0" in ids
        # Claude surfaces via the invocable inference-profile id...
        assert "eu.anthropic.claude-opus-4-8" in ids
        # ...and NOT via the non-invocable bare foundation-model id.
        assert "anthropic.claude-opus-4-8" not in ids
        # Embedding profile is excluded.
        assert "eu.cohere.embed-v4:0" not in ids
        # The profile entry carries a friendly name + derived provider.
        prof = next(m for m in body["models"]
                    if m["id"] == "eu.anthropic.claude-opus-4-8")
        assert prof["provider"] == "Anthropic"
        assert prof["name"] == "EU Anthropic Claude Opus 4.8"

    def test_returns_fallback_list_when_boto3_raises(
        self, client, authed_headers, monkeypatch, blueprint_app,
    ):
        from src.api.blueprints.chat import routes as chat_routes

        blueprint_app.chat_providers_service.get_effective_config.return_value = {
            "CHAT_LLM_PROVIDER": "aws_bedrock", "CHAT_LLM_MODEL": "",
            "CHAT_LLM_API_KEY": "", "CHAT_LLM_BASE_URL": "",
            "CHAT_PROMPT_CACHE": True, "CHAT_LLM_THINKING_ENABLED": False,
            "CHAT_LLM_AWS_REGION": "us-east-1",
            "CHAT_LLM_AWS_AUTH": {"auth_mode": "access_keys",
                                  "access_key_id": "AKIA", "secret_access_key": "s"},
        }

        def boom(region, **_kw):
            raise RuntimeError("no creds")

        monkeypatch.setattr(chat_routes, "_bedrock_control_client", boom)
        resp = client.get(
            "/api/chat/admin/bedrock/models?region=us-east-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["source"] == "fallback"
        assert "error" in body
        assert len(body["models"]) > 0

    def test_returns_fallback_when_no_saved_credentials(
        self, client, authed_headers, blueprint_app,
    ):
        # Service raises ValueError if there's no saved bedrock config.
        blueprint_app.chat_providers_service.get_effective_config.side_effect = (
            ValueError("AWS region is not set.")
        )
        resp = client.get(
            "/api/chat/admin/bedrock/models?region=us-east-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["source"] == "fallback"
        assert "AWS region" in body["error"]
        assert len(body["models"]) > 0

    def test_requires_admin_role(self, client, authed_headers):
        resp = client.get(
            "/api/chat/admin/bedrock/models?region=us-east-1",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_requires_region_param(
        self, client, authed_headers, blueprint_app,
    ):
        resp = client.get(
            "/api/chat/admin/bedrock/models",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["source"] == "fallback"
        assert "region" in body["error"].lower()

    def test_fallback_list_has_expected_shape(self):
        """Snapshot the curated fallback model list so accidental edits to
        the constant get caught. The actual IDs are expected to drift over
        time — bump the snapshot intentionally when refreshing."""
        from src.api.blueprints.chat import routes as chat_routes
        fallback = chat_routes._BEDROCK_FALLBACK_MODELS
        assert len(fallback) >= 5, "fallback list shrank suspiciously"
        for m in fallback:
            assert set(m.keys()) == {"id", "name", "provider"}, m
            assert m["id"]
            assert m["name"]
            assert m["provider"]
        # At least one Claude model should be present so the dropdown isn't
        # useless for the most common case.
        assert any("anthropic." in m["id"] for m in fallback)
