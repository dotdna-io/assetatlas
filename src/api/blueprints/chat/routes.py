# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chat REST routes — sessions CRUD, get/list, rename, delete, feedback, stream."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, current_app, request, stream_with_context
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

logger = logging.getLogger(__name__)

from src.api.middleware.error_handler import (
    NotFoundError, ServiceUnavailableError, handle_errors,
)
from src.api.utils.metrics import track_chat_feedback, track_chat_rate_limit
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin

from .admin_config.models import (
    AdminChatConfigUpdate, AdminChatTestRequest, ProviderConfigUpdate,
)
from .error_messages import describe_stream_error
from .models import (AutoTitleRequest, CreateSessionRequest,
                     DEFAULT_SESSION_TITLE, FeedbackRequest,
                     RenameSessionRequest, StreamTurnRequest)


chat_bp = Blueprint("chat", __name__, url_prefix="/api/chat")

CHAT_NOT_CONFIGURED = "CHAT_NOT_CONFIGURED"

# Endpoints exempt from the chat_service guard. /admin/config is the
# bootstrap path admins use to configure a provider when chat_service hasn't
# initialized yet — guarding it is the chicken-and-egg bug we exist to fix.
_CHAT_SERVICE_EXEMPT_PATHS = (
    "/api/chat/admin/config",
    "/api/chat/admin/config/test",
)


def _heal_chat_service() -> None:
    """Build this worker's chat_service on demand once a provider exists.

    gunicorn runs four workers and each builds its own chat_service in
    `create_app()`. On a fresh install none of them can — no provider is
    configured yet — so all four hold None. The admin's later save repairs only
    the worker that happened to serve that PATCH, leaving the other three to
    503 forever: chat could be turned off live but not on (#1124).

    The DB is the one thing every worker can see, so this reuses the staleness
    key the warm path already rebuilds its client from —
    `get_active_config_fingerprint`, which reads SQL directly precisely so a
    config write propagates to workers that didn't handle it. Building is
    retried only when that key moves, which matters both ways: an install with
    no API key yet doesn't rebuild the tool registry on every request, and a
    corrected key still reaches every worker without a container restart.
    """
    if not current_app.config.get("CHAT_ENABLED", False):
        return
    providers_service = getattr(current_app, "chat_providers_service", None)
    if providers_service is None:
        return

    app = current_app._get_current_object()
    fingerprint = providers_service.get_active_config_fingerprint()
    if getattr(app, "chat_init_attempted_fingerprint", None) == fingerprint:
        return

    from src.api.factory import init_chat_rate_limiter, init_chat_service
    # should_log=False: init_chat_service already treats the fresh-install case
    # as a warning rather than a fault, and the boot-time line was emitted once
    # per worker at startup — repeating it per heal is noise (#1104).
    app.chat_init_attempted_fingerprint = fingerprint
    init_chat_service(app, should_log=False)
    init_chat_rate_limiter(app, should_log=False)

    if app.chat_service is None:
        return
    # Only reachable once per worker: nothing sets chat_service back to None
    # after a successful build, so the guard stops calling this.
    logger.info(
        "Chat service initialized on demand in this worker "
        "(provider=%s, model=%s)",
        app.chat_service.llm.provider, app.chat_service.llm.model,
    )


@chat_bp.before_request
def _require_chat_service():
    """Reject user-facing chat requests with a structured 503 when no LLM
    provider is configured. The exception flows through `@handle_errors`
    via Flask's error handler stack, so the response shape matches the rest
    of the API (`{error, message, details}`)."""
    path = request.path
    if any(path == p or path.startswith(p + "/") for p in _CHAT_SERVICE_EXEMPT_PATHS):
        return None
    if getattr(current_app, "chat_service", None) is None:
        # Cold worker, or a genuinely unconfigured install — only the DB can
        # tell those apart, so ask it before giving up.
        _heal_chat_service()
    if getattr(current_app, "chat_service", None) is None:
        raise ServiceUnavailableError(
            "Chat service is not configured. Configure an LLM provider "
            "under Settings → Chatbot.",
            details={"code": CHAT_NOT_CONFIGURED},
        )
    # A client may still be in memory, but if the admin has disabled every
    # provider (active_provider = none) chat is off — 503 on every worker so
    # none keeps answering on a now-stale client.
    providers_service = getattr(current_app, "chat_providers_service", None)
    if (
        providers_service is not None
        and providers_service.get_active_provider() is None
    ):
        raise ServiceUnavailableError(
            "Chat service is not configured. Configure an LLM provider "
            "under Settings → Chatbot.",
            details={"code": CHAT_NOT_CONFIGURED},
        )
    return None


@chat_bp.errorhandler(ServiceUnavailableError)
def _handle_service_unavailable(exc: ServiceUnavailableError):
    """before_request handlers don't pass through route-level `@handle_errors`,
    so wire the same envelope here for ServiceUnavailableError specifically."""
    return exc.to_dict(), exc.status_code


@chat_bp.route("/sessions", methods=["POST"])
@jwt_required()
@handle_errors
@validate_request(CreateSessionRequest)
@response_wrapper
def create_session(validated_data: CreateSessionRequest):
    """Create a new chat session for the authenticated user."""
    user_id = get_jwt_identity()
    s = current_app.chat_service.create_session(
        user_id=user_id, title=validated_data.title,
    )
    return {
        "session_id": s.id,
        "title": s.title,
        "created_at": s.created_at.isoformat(),
    }


@chat_bp.route("/sessions", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def list_sessions():
    """List the authenticated user's chat sessions, paginated."""
    user_id = get_jwt_identity()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = max(1, min(100, request.args.get("per_page", 20, type=int) or 20))
    out = current_app.chat_service.repo.list_sessions(
        user_id=user_id, page=page, per_page=per_page,
    )
    return {
        "sessions": [_session_to_dict(s) for s in out.sessions],
        "total": out.total,
        "page": page,
        "per_page": per_page,
    }


@chat_bp.route("/sessions/<session_id>", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def get_session(session_id: str):
    """Return one session plus its full message history (if owned by user)."""
    user_id = get_jwt_identity()
    repo = current_app.chat_service.repo
    s = repo.load_session(session_id, user_id=user_id)
    if s is None:
        raise NotFoundError("Session not found")
    msgs = repo.load_messages(session_id)
    # Tool results (including artifacts) live on chat_tool_calls, not on
    # the messages themselves. Join them back in so the frontend can
    # re-render charts when a past conversation is reopened without an
    # extra round-trip.
    tool_calls_by_msg = repo.load_tool_calls_by_message(session_id)
    return {
        "session": _session_to_dict(s),
        "messages": [
            _message_to_dict(m, tool_calls_by_msg.get(m.id, []))
            for m in msgs
        ],
    }


@chat_bp.route("/sessions/<session_id>", methods=["PATCH"])
@jwt_required()
@handle_errors
@validate_request(RenameSessionRequest)
@response_wrapper
def rename_session(session_id: str,
                   validated_data: RenameSessionRequest):
    """Rename a session owned by the authenticated user."""
    user_id = get_jwt_identity()
    s = current_app.chat_service.repo.rename_session(
        session_id, user_id=user_id, title=validated_data.title,
    )
    if s is None:
        raise NotFoundError("Session not found")
    return _session_to_dict(s)


_AUTOTITLE_TIMEOUT_SECONDS = 15.0


@chat_bp.route("/sessions/<session_id>/autotitle", methods=["POST"])
@jwt_required()
@handle_errors
@validate_request(AutoTitleRequest)
@response_wrapper
def autotitle_session(session_id: str,
                      validated_data: AutoTitleRequest):
    """Generate and persist a short LLM-derived title for a session.

    Only overwrites the session title when it is still the default, so a
    user who renamed the session manually is never silently retitled.
    Provider errors and timeouts fall back to the default — returned as
    the unchanged session so the UI can render from a single source of
    truth without differentiating the failure mode."""
    user_id = get_jwt_identity()
    repo = current_app.chat_service.repo
    session = repo.load_session(session_id, user_id=user_id)
    if session is None:
        raise NotFoundError("Session not found")
    if session.title != DEFAULT_SESSION_TITLE:
        return _session_to_dict(session)

    svc = current_app.chat_service
    try:
        title = asyncio.run(asyncio.wait_for(
            svc.generate_title(validated_data.user_text),
            timeout=_AUTOTITLE_TIMEOUT_SECONDS,
        ))
    except asyncio.TimeoutError:
        logger.warning("Auto-title generation timed out after %.1fs",
                       _AUTOTITLE_TIMEOUT_SECONDS)
        title = None
    except Exception:
        logger.exception("Auto-title generation failed")
        title = None
    if not title:
        # asyncio.wait_for cancels generate_title on timeout BEFORE its own
        # _fallback_title runs, so derive one from the opening message here —
        # otherwise a slow or erroring model (e.g. a reasoning model that blows
        # the 15s budget) leaves the session permanently on the default title.
        title = svc.fallback_title(validated_data.user_text)
    if not title:
        return _session_to_dict(session)
    updated = repo.rename_session(session_id, user_id=user_id, title=title)
    return _session_to_dict(updated or session)


@chat_bp.route("/sessions/<session_id>", methods=["DELETE"])
@jwt_required()
@handle_errors
@response_wrapper
def delete_session(session_id: str):
    """Soft-delete a session owned by the authenticated user."""
    user_id = get_jwt_identity()
    ok = current_app.chat_service.repo.delete_session(
        session_id, user_id=user_id,
    )
    if not ok:
        raise NotFoundError("Session not found")
    return {"ok": True}


@chat_bp.route("/feedback", methods=["POST"])
@jwt_required()
@handle_errors
@validate_request(FeedbackRequest)
@response_wrapper
def feedback(validated_data: FeedbackRequest):
    """Record thumbs up/down on an assistant message."""
    user_id = get_jwt_identity()
    current_app.chat_service.repo.upsert_feedback(
        message_id=validated_data.message_id,
        user_id=user_id,
        rating=validated_data.rating,
        comment=validated_data.comment,
    )
    track_chat_feedback(validated_data.rating)
    return {"ok": True}


@chat_bp.route("/usage", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def usage():
    """Return today's chat usage and remaining quota for the user."""
    user_id = get_jwt_identity()
    rl = current_app.chat_rate_limiter
    count, remaining = rl.current(user_id)
    return {"messages_today": count, "remaining": remaining,
            "limit": rl.per_day}


@chat_bp.route("/admin/usage", methods=["GET"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def admin_usage():
    """Aggregated chat activity for the admin dashboard.

    Query param ``days`` (default 7, clamped to 1..90) scopes the window.
    """
    days = max(1, min(90, request.args.get("days", 7, type=int) or 7))
    return current_app.chat_service.repo.usage_summary(days=days)


_ALLOWED_AUDIT_ROLES = {"user", "assistant", "tool", "system"}


@chat_bp.route("/admin/audit", methods=["GET"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def admin_audit():
    """Paginated audit view of chat_messages with optional filters."""
    days = max(1, min(90, request.args.get("days", 7, type=int) or 7))
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = max(1, min(100, request.args.get("per_page", 50, type=int) or 50))
    user_id = request.args.get("user_id") or None
    role = request.args.get("role") or None
    if role is not None and role not in _ALLOWED_AUDIT_ROLES:
        # Fail loud — silently dropping a bogus role would return
        # misleading "no results" without telling the caller why.
        from src.api.middleware.error_handler import ValidationError
        raise ValidationError(
            f"Invalid role {role!r}; expected one of "
            f"{sorted(_ALLOWED_AUDIT_ROLES)}"
        )
    return current_app.chat_service.repo.audit_messages(
        days=days, page=page, per_page=per_page,
        user_id=user_id, role=role,
    )


# ---------------------------------------------------------------------------
# Admin — LLM configuration (view/edit/test) — see issue #636
# ---------------------------------------------------------------------------


@chat_bp.route("/admin/config", methods=["GET"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def admin_get_config():
    """Return the per-provider config: active provider plus all four rows
    (with API keys masked)."""
    svc = current_app.chat_providers_service
    cfg = svc.get_admin_config(
        chat_enabled=bool(current_app.config.get("CHAT_ENABLED", False)),
    )
    return cfg.model_dump()


@chat_bp.route("/admin/config", methods=["PATCH"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(AdminChatConfigUpdate)
@response_wrapper
def admin_patch_config(validated_data: AdminChatConfigUpdate):
    """Apply a partial update to one or more providers and/or change the
    active provider. The live LLM client is rebuilt only when the change
    affects the active provider — switching active or editing the active
    provider's row.

    Body shape::

        {
          "active_provider": "openai",          # optional
          "providers": {                         # optional
            "openai": { "model": "...", "api_key": "...", ... }
          }
        }
    """
    from src.api.blueprints.chat.llm import make_llm_client
    from src.api.middleware.error_handler import ValidationError

    svc = current_app.chat_providers_service
    user_id = get_jwt_identity()
    claims = get_jwt() or {}
    username = claims.get("username") or user_id
    ip = request.remote_addr

    # Distinguish "active_provider omitted" from "active_provider: null"
    # (the latter deactivates every provider) via the fields the client sent.
    active_field_set = "active_provider" in validated_data.model_fields_set
    new_active = validated_data.active_provider
    cleaned_updates: Dict[str, ProviderConfigUpdate] = (
        validated_data.providers or {}
    )

    for p, update in cleaned_updates.items():
        svc.update_provider(p, update)

    current_active = svc.get_active_provider()
    deactivating = active_field_set and new_active is None
    switching = new_active is not None and new_active != current_active
    target_active = new_active if new_active is not None else current_active
    # Rebuild the live client only when there's an active provider to build
    # for — on a switch, or when the active provider's own row was edited.
    # Never when deactivating (there's nothing to build).
    needs_rebuild = (not deactivating) and (
        switching
        or (target_active is not None and target_active in cleaned_updates)
    )

    if needs_rebuild:
        try:
            effective = svc.get_effective_config(target_active)
            new_client = make_llm_client(effective)
        except Exception as e:
            logger.exception(
                "Failed to build LLM client for provider %s; "
                "active toggle and rebuild aborted", target_active,
            )
            raise ValidationError(
                f"Config saved but client build failed for "
                f"{target_active}: {e.__class__.__name__}: {e}"
            )
        # Persist active_provider only after the client builds, so a
        # failed switch leaves the system on the previous working provider.
        if new_active is not None and new_active != current_active:
            svc.set_active_provider(
                new_active, user_id=user_id, username=username, ip=ip,
            )
        chat_service = getattr(current_app, "chat_service", None)
        if chat_service is None:
            # First-time setup: chat_service was None at startup because no
            # provider was configured. init_chat_service re-reads the effective
            # config and builds its own client, so we don't need rebuild_llm —
            # the freshly-built service already uses the latest config.
            from src.api.factory import (
                init_chat_rate_limiter, init_chat_service,
            )
            init_chat_service(current_app._get_current_object(), should_log=False)
            init_chat_rate_limiter(current_app._get_current_object(), should_log=False)
        else:
            chat_service.rebuild_llm(new_client)
        logger.info("Chat LLM rebuilt (provider=%s, model=%s) via admin config",
                    new_client.provider, new_client.model)

    # Deactivation: persist "no active provider". No client to build; the
    # before_request guard will 503 user-facing chat on every worker.
    if deactivating and current_active is not None:
        svc.set_active_provider(
            None, user_id=user_id, username=username, ip=ip,
        )

    return svc.get_admin_config(
        chat_enabled=bool(current_app.config.get("CHAT_ENABLED", False)),
    ).model_dump()


@chat_bp.route("/admin/config/test", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(AdminChatTestRequest)
@response_wrapper
def admin_test_config(validated_data: AdminChatTestRequest):
    """Probe a provider with one low-token round-trip without persisting.

    Returns ``{ok, detail, sample_text, provider, model, ...}``. Provider
    errors surface as ``ok=False``, never 500."""
    from src.api.blueprints.chat.llm import make_llm_client
    from src.api.blueprints.chat.llm.base import LLMMessage, ToolSpec

    provider = validated_data.provider
    svc = current_app.chat_providers_service
    # Tolerate missing rows / empty api keys here — the test should still
    # attempt a build so the operator sees the real provider error.
    try:
        effective = svc.get_effective_config(provider)
    except ValueError:
        effective = {
            "CHAT_LLM_PROVIDER": provider,
            "CHAT_LLM_MODEL": "",
            "CHAT_LLM_API_KEY": "",
            "CHAT_LLM_BASE_URL": "",
            "CHAT_PROMPT_CACHE": True,
            "CHAT_LLM_THINKING_ENABLED": True,
        }

    overlay_map = {
        "model": "CHAT_LLM_MODEL",
        "api_key": "CHAT_LLM_API_KEY",
        "base_url": "CHAT_LLM_BASE_URL",
        "prompt_cache": "CHAT_PROMPT_CACHE",
        "thinking_enabled": "CHAT_LLM_THINKING_ENABLED",
        "foundry_protocol": "CHAT_LLM_FOUNDRY_PROTOCOL",
    }
    body = validated_data.model_dump(exclude_none=True)
    for field, env_key in overlay_map.items():
        if field in body:
            effective[env_key] = body[field]

    # Bedrock's region + auth secrets aren't in overlay_map (they compose an
    # encrypted JSON blob, not flat env keys). Merge the draft AWS fields over
    # the saved blob here — otherwise Test validates the saved credentials, or
    # fails on first-time setup, instead of what's on the form.
    if provider == "aws_bedrock":
        _aws_fields = (
            "aws_region", "auth_mode", "access_key_id", "secret_access_key",
            "role_arn", "external_id", "base_access_key_id",
            "base_secret_access_key",
        )
        draft = ProviderConfigUpdate(
            **{k: v for k, v in body.items() if k in _aws_fields}
        )
        effective.update(svc.bedrock_test_overlay(draft))

    try:
        client = make_llm_client(effective)
    except Exception as e:
        return {
            "ok": False,
            "detail": f"Cannot construct client: "
                      f"{e.__class__.__name__}: {e}",
            "sample_text": "",
            "provider": provider,
            "model": effective["CHAT_LLM_MODEL"],
        }

    # Probe with a trivial tool so the test exercises the same Converse
    # tool-use path every real chat turn requires (service.stream_turn always
    # sends the registry's tools). A model that can't do tool use — e.g.
    # mistral.mistral-7b-instruct-v0:2 — then fails the test instead of
    # passing it and dying on the first real message (issue #695).
    probe_tool = ToolSpec(
        name="noop",
        description="Health-check probe. Do not call this tool; reply with "
                    "text instead.",
        input_schema={"type": "object", "properties": {},
                      "additionalProperties": False},
    )

    async def _ping() -> Dict[str, Any]:
        # 512 tokens accommodates reasoning models (Qwen3, DeepSeek-R1,
        # etc.) whose hidden reasoning pass can consume hundreds of
        # tokens before any final content emerges.
        async with await client.stream(
            system_prompt="You are a terse assistant. Reply with exactly: OK.",
            messages=[LLMMessage(role="user", content="ping")],
            tools=[probe_tool], max_output_tokens=512,
        ) as s:
            async for _ in s:
                pass
            return {
                "stop_reason": s.result.stop_reason,
                "text": s.result.final_text,
                "input_tokens": s.result.usage.input_tokens,
                "output_tokens": s.result.usage.output_tokens,
            }

    try:
        # 30s ceiling lets reasoning models finish their thinking pass.
        result = asyncio.run(asyncio.wait_for(_ping(), timeout=30.0))
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "detail": "Test timed out after 30 seconds",
            "sample_text": "",
            "provider": provider,
            "model": effective["CHAT_LLM_MODEL"],
        }
    except Exception as e:
        logger.exception("Chat config test failed")
        return {
            "ok": False,
            "detail": f"{e.__class__.__name__}: {e}",
            "sample_text": "",
            "provider": provider,
            "model": effective["CHAT_LLM_MODEL"],
        }

    text = result.get("text") or ""
    stop_reason = result.get("stop_reason")
    called_tool = stop_reason == "tool_use"
    if text:
        detail = "Streaming round-trip succeeded"
    elif called_tool:
        # A tool-capable model may answer the probe by invoking the tool
        # instead of replying with text — that still proves tool-use support,
        # which is the capability this test exists to verify (issue #695).
        detail = "Streaming round-trip succeeded (model called the probe tool)"
    elif stop_reason == "max_tokens":
        detail = ("Reply cut off before any content (hit token cap during "
                  "reasoning). If this is a reasoning model, bump "
                  "CHAT_MAX_OUTPUT_TOKENS in .env.")
    else:
        detail = ("Empty reply. If this is a reasoning model, content may "
                  "be filtered into a separate reasoning field the client "
                  "doesn't surface.")
    return {
        "ok": bool(text) or called_tool,
        "detail": detail,
        "sample_text": text[:200],
        "stop_reason": stop_reason,
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "provider": provider,
        "model": effective["CHAT_LLM_MODEL"],
    }


# How often the worker checks whether the client has gone away. A
# disconnect (Stop button / closed tab) is noticed within this window and
# then cancels the in-flight turn. Small enough to feel immediate, large
# enough to be cheap.
_CANCEL_POLL_SECONDS = 0.1


@chat_bp.route("/stream", methods=["POST"])
@jwt_required()
@handle_errors
@validate_request(StreamTurnRequest)
def stream_turn_route(validated_data: StreamTurnRequest):
    """Stream one chat turn as Server-Sent Events.

    Emits SSE frames matching ChatService.StreamEvent kinds:
    message.start, delta, tool.start, tool.end, message.end, error, done.

    Note: no @response_wrapper — the response is a streaming Flask Response,
    not a dict, so wrapping would corrupt the SSE payload.
    """
    user_id = get_jwt_identity()
    role = _current_user_role()
    rl = current_app.chat_rate_limiter

    allowed, count, remaining = rl.check_and_incr(user_id)
    if not allowed:
        track_chat_rate_limit("daily")
        return _sse_error_response("rate_limited",
                                   count=count, remaining=remaining)

    if not rl.acquire_concurrency_lock(user_id):
        track_chat_rate_limit("concurrent")
        return _sse_error_response("concurrent_turn_in_progress")

    svc = current_app.chat_service
    session_id = validated_data.session_id
    user_text = validated_data.message
    # Bound the worker join so client disconnects don't tie up the request
    # for the full inner turn timeout. Worker is daemon so a leaked thread
    # can't block process exit.
    join_timeout = float(svc.turn_timeout) + 5.0

    def _iter_sync():
        # Bridge async generator -> sync iterator via a worker thread + queue.
        # Flask's sync workers can't iterate `async def` directly.
        q: "queue.Queue[object]" = queue.Queue()
        SENTINEL = object()
        # Set when the client goes away (GeneratorExit in the drain loop's
        # finally). The worker runs a watcher task that polls this and
        # cancels the in-flight turn, so a Stop / closed tab actually halts
        # the LLM stream and any running tool instead of letting the turn
        # finish in the background and persist an answer nobody asked for.
        client_gone = threading.Event()

        # P1 (#735): release this request's DB scope before we block on the
        # stream. JWT/role lookups earlier in the request acquired a pooled
        # connection into THIS (request) thread's scope; holding it idle for
        # the whole turn lets the server drop it, and the teardown
        # end_scope() commit would then 500. The worker thread runs all
        # chat/tool DB work with its own per-call pool connections
        # (_scope_state is thread-local), so the drain loop needs no scope.
        # end_scope() is re-entrant and no-ops if no scope is active.
        current_app.connection_manager.end_scope()

        async def driver():
            turn_task = asyncio.current_task()

            async def _watch_for_disconnect():
                # Poll the cross-thread flag; cancelling the turn task
                # raises CancelledError at the LLM/tool await point, which
                # unwinds stream_turn cleanly (closing the provider stream).
                while not client_gone.is_set():
                    await asyncio.sleep(_CANCEL_POLL_SECONDS)
                turn_task.cancel()

            watcher = asyncio.create_task(_watch_for_disconnect())
            try:
                async for ev in svc.stream_turn(
                    session_id=session_id, user_text=user_text,
                    user_role=role, user_id=user_id,
                ):
                    q.put(_sse(ev.kind, ev.payload))
            except asyncio.CancelledError:
                # Client disconnected mid-turn — there's nobody left to read
                # an error frame, so unwind quietly. CancelledError is a
                # BaseException, so the `except Exception` below never sees
                # it.
                pass
            except Exception as e:
                # Full traceback to server log — the raw provider error stays
                # here only. The client gets a safe, model-agnostic hint via
                # describe_stream_error so the UI shows something actionable
                # instead of the bare "stream_failed" code, without leaking
                # provider internals to users (issue #695).
                logger.exception(
                    "Chat stream failed for session=%s user=%s: %s",
                    session_id, user_id, e,
                )
                q.put(_sse("error", {"code": "stream_failed",
                                     "message": describe_stream_error(str(e))}))
                q.put(_sse("done", {}))
            finally:
                watcher.cancel()
                q.put(SENTINEL)

        def target():
            try:
                asyncio.run(driver())
            finally:
                rl.release_concurrency_lock(user_id)

        t = threading.Thread(target=target, daemon=True)
        t.start()
        try:
            while True:
                item = q.get()
                if item is SENTINEL:
                    break
                yield item
        finally:
            # Reaching here via GeneratorExit (client disconnected before
            # the turn finished) rather than a clean SENTINEL: tell the
            # worker to cancel so the LLM/tool work stops. On a clean finish
            # the worker is already done and this is a harmless no-op.
            client_gone.set()
            # Ensure the worker's finally (lock release) has run before
            # the response generator returns. Without this, callers can
            # observe the response complete while the lock is still held.
            # Bounded so a client disconnect doesn't block the request
            # teardown for the full inner turn timeout.
            t.join(timeout=join_timeout)

    return Response(stream_with_context(_iter_sync()),
                    mimetype="text/event-stream")


def _sse(event: str, data: dict) -> str:
    """Format one Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _sse_error_response(code: str, *, status: int = 429, **payload) -> Response:
    """Build a 2-frame SSE error response (error + done) with the given code."""
    body = _sse("error", {"code": code, **payload}) + _sse("done", {})
    return Response(body, status=status, mimetype="text/event-stream")


def _current_user_role() -> str:
    claims = get_jwt() or {}
    return claims.get("role", "user")


def _session_to_dict(s):
    return {
        "id": s.id,
        "title": s.title,
        "model": s.model,
        "provider": s.provider,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _message_to_dict(m, tool_call_rows=None):
    """Serialise a MessageRow, optionally folding each tool_use block's
    ``artifact`` in from its paired chat_tool_calls row. Pairing is
    positional — same invariant used by service._to_llm_messages."""
    tool_calls = m.tool_calls
    if tool_calls and tool_call_rows:
        enriched = []
        for idx, tu in enumerate(tool_calls):
            if not isinstance(tu, dict):
                enriched.append(tu)
                continue
            row = tool_call_rows[idx] if idx < len(tool_call_rows) else None
            if row is not None and row.artifact is not None:
                # Shallow copy — don't mutate the persisted tool_calls
                # JSON loaded via the DB row reference.
                enriched.append({**tu, "artifact": row.artifact})
            else:
                enriched.append(tu)
        tool_calls = enriched

    return {
        "id": m.id,
        "seq": m.seq,
        "role": m.role,
        "content": m.content,
        "tool_calls": tool_calls,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "created_at": m.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Admin — AWS Bedrock live model list
# ---------------------------------------------------------------------------

# Curated static fallback for the model dropdown when boto3 fails. Covers
# the common Claude / Llama / Mistral / Cohere / Titan models customers
# typically have entitlement for in the US/EU regions.
_BEDROCK_FALLBACK_MODELS = [
    {"id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
     "name": "Claude 3.5 Sonnet (v2)", "provider": "Anthropic"},
    {"id": "anthropic.claude-3-5-haiku-20241022-v1:0",
     "name": "Claude 3.5 Haiku", "provider": "Anthropic"},
    {"id": "meta.llama3-1-70b-instruct-v1:0",
     "name": "Llama 3.1 70B Instruct", "provider": "Meta"},
    {"id": "meta.llama3-1-8b-instruct-v1:0",
     "name": "Llama 3.1 8B Instruct", "provider": "Meta"},
    {"id": "mistral.mistral-large-2402-v1:0",
     "name": "Mistral Large", "provider": "Mistral AI"},
    {"id": "cohere.command-r-plus-v1:0",
     "name": "Command R+", "provider": "Cohere"},
    {"id": "amazon.titan-text-premier-v1:0",
     "name": "Titan Text Premier", "provider": "Amazon"},
]


def _bedrock_control_client(region: str, *, auth: Dict[str, Any]):
    """Construct an authenticated boto3 ``bedrock`` (control plane) client
    from the same auth dict shape that make_llm_client uses for the runtime
    client. Kept as a module-level function so tests can monkeypatch it."""
    import boto3
    from src.api.blueprints.chat.llm.aws_credentials import resolve_aws_credentials
    client_kwargs = resolve_aws_credentials(
        region, auth=auth, session_name_prefix="assetatlas-chat-listmodels",
    )
    return boto3.client("bedrock", **client_kwargs)


def _streamable_text_fm(summary: Dict[str, Any]) -> bool:
    """True for a foundation-model summary we can actually drive through
    ``converse_stream``: active (not LEGACY), supports response streaming, and
    produces text output. AWS omits ``responseStreamingSupported`` on some
    entries, so a missing value is treated as supported."""
    return (
        (summary.get("modelLifecycle") or {}).get("status") != "LEGACY"
        and summary.get("responseStreamingSupported", True)
        and "TEXT" in (summary.get("outputModalities") or ["TEXT"])
    )


def _profile_foundation_model_id(profile: Dict[str, Any]) -> Optional[str]:
    """Return the foundation-model id a system-defined inference profile routes
    to (e.g. ``anthropic.claude-opus-4-8`` from its model ARN). Cross-region
    profiles list one ARN per backing region; they share the same model-id
    suffix, so the first is enough."""
    for entry in profile.get("models") or []:
        arn = entry.get("modelArn") or ""
        if "foundation-model/" in arn:
            return arn.split("foundation-model/", 1)[1]
    return None


@chat_bp.route("/admin/bedrock/models", methods=["GET"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def admin_list_bedrock_models():
    """Return selectable Bedrock text chat models for the saved Bedrock row's
    credentials + the supplied region: on-demand foundation models *plus*
    system-defined cross-region inference profiles (the only way current Claude
    models are invocable). Falls back to a curated static list when the AWS
    call fails (auth, network, region) — the UI shows the fallback hint."""
    region = (request.args.get("region") or "").strip()
    if not region:
        return {"models": _BEDROCK_FALLBACK_MODELS, "source": "fallback",
                "error": "region query parameter is required"}

    svc = current_app.chat_providers_service
    try:
        eff = svc.get_effective_config("aws_bedrock")
        auth = eff["CHAT_LLM_AWS_AUTH"]
    except Exception as exc:
        logger.info("Bedrock models live-fetch skipped: %s", exc)
        return {"models": _BEDROCK_FALLBACK_MODELS, "source": "fallback",
                "error": str(exc)}

    try:
        control = _bedrock_control_client(region, auth=auth)
        # NB: do NOT pass byInferenceType="ON_DEMAND" here. Current Claude
        # models on Bedrock are invocable only via a cross-region inference
        # profile (inferenceTypesSupported=["INFERENCE_PROFILE"]); that filter
        # makes AWS drop them server-side, leaving only on-demand models like
        # Mistral. We fetch all TEXT models and decide per-entry below.
        summaries = (control.list_foundation_models(
            byOutputModality="TEXT",
        ).get("modelSummaries")) or []
    except Exception as exc:
        logger.warning("list_foundation_models failed: %s", exc)
        return {"models": _BEDROCK_FALLBACK_MODELS, "source": "fallback",
                "error": f"{exc.__class__.__name__}: {exc}"}

    fm_by_id = {m["modelId"]: m for m in summaries if m.get("modelId")}

    models: List[Dict[str, str]] = []
    seen: set = set()

    def _add(model_id: Optional[str], name: Optional[str],
             provider: Optional[str]) -> None:
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append({"id": model_id, "name": name or model_id,
                           "provider": provider or ""})

    # 1) On-demand foundation models — invocable directly by their bare
    #    modelId (Mistral, Amazon Nova on-demand, ...).
    for m in summaries:
        if "ON_DEMAND" in (m.get("inferenceTypesSupported") or []) \
                and _streamable_text_fm(m):
            _add(m.get("modelId"), m.get("modelName"), m.get("providerName"))

    # 2) System-defined (cross-region) inference profiles — the only way to
    #    invoke current Claude models. The inferenceProfileId (e.g.
    #    "eu.anthropic.claude-opus-4-8") is itself a valid converse_stream
    #    modelId. Best-effort: skip silently when the creds lack
    #    bedrock:ListInferenceProfiles. We only surface a profile whose backing
    #    foundation model is a streamable TEXT model — this drops embedding
    #    profiles (e.g. cohere.embed, EMBEDDING output) and legacy models.
    #    Video-capable models that also emit TEXT (Nova, TwelveLabs Pegasus)
    #    pass, same as a byOutputModality="TEXT" query would include them.
    try:
        profiles = (control.list_inference_profiles(
            typeEquals="SYSTEM_DEFINED",
        ).get("inferenceProfileSummaries")) or []
    except Exception as exc:
        logger.info("list_inference_profiles skipped: %s", exc)
        profiles = []

    for p in profiles:
        if (p.get("status") or "ACTIVE") != "ACTIVE":
            continue
        fm = fm_by_id.get(_profile_foundation_model_id(p))
        if fm is None or not _streamable_text_fm(fm):
            continue
        _add(p.get("inferenceProfileId"), p.get("inferenceProfileName"),
             fm.get("providerName"))

    return {"models": models, "source": "live"}
