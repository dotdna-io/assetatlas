# SPDX-License-Identifier: AGPL-3.0-or-later
"""ChatService — orchestrates LLM streaming + tool loop.

Drives one turn per :meth:`ChatService.stream_turn` call: persists the user
message, calls the LLM with the full conversation plus the role-filtered
tool registry, dispatches tool calls when the model asks for them, feeds
results back, and repeats until the model emits ``end_turn`` (or we hit the
per-turn iteration cap or the wall-clock timeout).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Set, Tuple

from src.api.utils.metrics import track_chat_rate_limit, track_chat_turn

from ._canonical import stable_json
from .error_messages import MAX_TOKENS_NOTICE, describe_stream_error
from .llm.base import LLMMessage, ToolSpec
from .prompts import build_system_prompt
from .redaction import redact_text
from .repository import ChatRepository
from .tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


StreamEventKind = Literal[
    "message.start", "delta", "tool.start", "tool.end",
    "message.end", "error", "done",
]

# Number of consecutive internal tool errors (status="error" — i.e. the
# handler raised an exception) after which the turn aborts with a clean
# error event rather than continuing to spend tokens. A single "ok" or
# "denied" / "timeout" in between resets the counter.
_INTERNAL_ERROR_ABORT_THRESHOLD = 3

# Stand-in tool result for a parallel tool_use block that never ran because the
# turn aborted mid-batch (see _INTERNAL_ERROR_ABORT_THRESHOLD). Every tool_use
# id must be answered or the provider rejects the follow-up request
# ("Expected toolResult blocks ... for the following Ids").
_ABORTED_TOOL_PLACEHOLDER = (
    "(not executed — turn aborted after repeated tool errors)"
)

# Advisory tag stamped on a tool result when the model has called the SAME
# tool with IDENTICAL args earlier in this turn. We still run the tool (so
# legitimate re-polls of status-style endpoints aren't masked) — the prefix
# just nudges the model to stop retrying the exact same call.
_REPEAT_CALL_PREFIX = (
    "[REPEAT of iteration {prev_iter} with identical args. Result below is "
    "the same as before — try a different tool, different args, or answer "
    "with current information.]\n\n"
)

# Advisory tag stamped when the model has called the same tool name N >= 3
# times within one turn (regardless of args). Catches the "permute the filter
# until it works" pattern that exact-repeat detection misses.
_FREQUENCY_ADVISORY_THRESHOLD = 3
_FREQUENCY_CALL_PREFIX = (
    "[Call #{call_n} to '{tool_name}' this turn. Previous attempts with "
    "different args did not produce an answer. If this one also doesn't, "
    "stop permuting args and either switch tools or answer with what you "
    "already have.]\n\n"
)

# Appended to the system prompt when the tool-loop gives up, to nudge the
# model into a user-facing final answer without calling more tools.
_SUMMARY_NUDGE = (
    "\n\n[SYSTEM: Tool budget exhausted ({cause}). Reply with plain text "
    "only — no tool calls. Summarise what the prior tool results show, or "
    "explain what you could not determine. Do not invent facts.]"
)

# Cap for the one-shot summary LLM call. Smaller than the main per-turn
# budget because "wrap up what you know" needs much less room than the
# original answer, and a reasoning-model runaway here would burn whatever
# is left of the parent turn_timeout.
_SUMMARY_MAX_OUTPUT_TOKENS = 800

# Tool-calling-native models (Qwen3-coder, some vLLM setups) sometimes emit
# literal <tool_call>…</tool_call> XML as delta.content when we force
# tools=[] — because the tools-off protocol differs per provider and the
# server-side parser has no registered tools to strip against. Scrub these
# blocks so the user never sees raw XML, and treat "only XML" as an empty
# summary so the apology fallback kicks in.
_TOOL_CALL_XML_RE = re.compile(
    r"<tool_call\b[^>]*>.*?</tool_call>|<function\b[^>]*>.*?</function>",
    re.DOTALL | re.IGNORECASE,
)
_MIN_SUMMARY_CHARS = 20

_SUMMARY_FALLBACK_APOLOGY = (
    "I couldn't complete that question within the tool-use budget for "
    "this turn (cause: {cause}). Any partial tables shown above are "
    "incomplete — please disregard them as an answer. Try narrowing the "
    "question (a single field or category), or call `list_fields` first if "
    "you were asking about a data-source-specific field."
)

# Auto-title tunables. 200 output tokens leaves headroom for reasoning models
# whose hidden chain-of-thought can consume most of a smaller budget before
# any title text emerges. The input cap keeps pasted dumps from burning the
# context window on a one-liner title request.
_TITLE_SYSTEM_PROMPT = (
    "You generate concise chat titles. Given the user's opening message, "
    "produce a 3 to 4 word title capturing its topic. Output rules: plain "
    "text only; no quotes, no emojis, no trailing punctuation, no prefix "
    "like 'Title:'. Use Title Case. Maximum 60 characters."
)
_TITLE_MAX_CHARS = 60
# Generous on purpose: reasoning models (e.g. Qwen3) spend output tokens on a
# <think> block BEFORE emitting the title. At 200 the budget was consumed
# entirely by reasoning, the call was cut off (stop_reason=max_tokens) with
# empty content, and every title fell back to the raw message (#735 P4 — seen on
# qwen3.6-35b-a3b, which needed ~544 tokens to think + answer). It's a cap, so
# non-reasoning providers stop at end_turn well under it and pay nothing extra.
_TITLE_MAX_OUTPUT_TOKENS = 2048
_TITLE_INPUT_MAX_CHARS = 2000


def _canonical_args(args: Any) -> str:
    """Stable string for within-turn tool-call deduplication. Length-capped
    because the LLM sometimes emits wildly nested argument objects."""
    return stable_json(args or {}, max_len=2000)


def _select_turn_tools(*, routing_enabled: bool, router: Any,
                       all_role_tools: List[ToolSpec], user_text: str,
                       role: str, loaded: Set[str],
                       top_k: Optional[int] = None) -> List[ToolSpec]:
    """Tools to advertise this iteration. Flag OFF or no router => every
    role-visible tool (today's behavior). Flag ON => router selection.
    ``top_k`` (when given) is the live retrieval breadth; None lets the router
    use its init-time default."""
    if not routing_enabled or router is None:
        return all_role_tools
    return router.select(user_text, role, loaded, top_k=top_k)


@dataclass
class StreamEvent:
    """One event emitted by :meth:`ChatService.stream_turn`."""
    kind: StreamEventKind
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _TurnMetrics:
    """Per-turn aggregates emitted to Prometheus at turn teardown."""
    status: str = "stream_failed"   # default if teardown never sets it
    input_tokens: int = 0
    output_tokens: int = 0


class ChatService:
    """Single-turn orchestration: LLM stream + tool dispatch + persistence."""

    def __init__(self, *, db: Any, llm: Any, registry: ToolRegistry,
                 providers_service: Any = None,
                 settings_manager: Any = None,
                 max_tool_iterations: int = 10,
                 max_output_tokens: int = 2000,
                 turn_timeout_seconds: int = 60,
                 turn_total_timeout_seconds: float = 90,
                 tool_router=None):
        self.repo = ChatRepository(db)
        self.llm = llm
        self.registry = registry
        self.tool_router = tool_router
        # Injected (not read via current_app) because the turn streams from a
        # daemon worker thread (see routes.py) whose missing Flask app context
        # makes current_app.settings_manager raise — which previously forced
        # tool routing permanently OFF regardless of the DB flag (PR #803).
        self.settings_manager = settings_manager
        self.max_iter = max_tool_iterations
        self.max_out = max_output_tokens
        # turn_timeout: max gap between two stream events (a single hung
        # LLM/tool call). turn_total_timeout: max wall-clock for the WHOLE turn
        # (all tool iterations + final answer) — a long fan-out emits events
        # steadily, so the per-event timeout alone never fires while the total
        # runs past a public gateway's request timeout and 504s. 0 disables it.
        self.turn_timeout = turn_timeout_seconds
        self.turn_total_timeout = turn_total_timeout_seconds
        # providers_service drives per-turn staleness detection so that
        # config writes made by the worker handling PATCH /chat/admin/config
        # propagate to the other gunicorn workers without a container
        # restart. Tests that don't exercise that path pass None.
        self._providers_service = providers_service
        # Fingerprint of the DB state this worker's self.llm was built from.
        # Primed at construction (one tiny SELECT) so the first chat turn on
        # a freshly-booted worker doesn't redundantly re-run make_llm_client
        # for config that hasn't changed since factory init built `llm`.
        self._config_fingerprint: Optional[Tuple[str, Optional[str]]] = None
        if providers_service is not None:
            try:
                self._config_fingerprint = (
                    providers_service.get_active_config_fingerprint()
                )
            except Exception:
                logger.debug(
                    "Failed to read initial chat config fingerprint; "
                    "first turn will trigger a rebuild attempt.",
                    exc_info=True,
                )
        # Serialises rebuilds within one worker — concurrent turns observing
        # the same staleness shouldn't each pay for a fresh make_llm_client.
        self._rebuild_lock = asyncio.Lock()
        # Strong references to fire-and-forget SDK close() tasks so they
        # aren't GC'd before completion. See rebuild_llm.
        self._pending_closes: "set[asyncio.Task]" = set()

    def create_session(self, *, user_id: str, title: str):
        return self.repo.create_session(user_id=user_id, title=title,
                                        model=self.llm.model,
                                        provider=self.llm.provider)

    async def generate_title(self, user_text: str) -> Optional[str]:
        """Ask the LLM for a short (3-4 word) title based on the user's
        opening message. Returns the cleaned title, or ``None`` only when
        input is blank or the provider produced nothing usable. Callers are
        responsible for imposing a wall-clock timeout and swallowing
        provider errors — mirrors the ``admin_test_config`` route's use of
        ``asyncio.run(asyncio.wait_for(...))``."""
        if not user_text or not user_text.strip():
            return None
        await self._ensure_current_client()
        # The title is a trivial 3-4 word task that needs no reasoning. On
        # qwen3-class models the hidden <think> block alone exceeds the route's
        # 15s wall-clock budget, so the call is cancelled and the session is
        # left untitled. Disable thinking for THIS call where the provider
        # supports the per-request switch (openai_compat / vLLM); the main chat
        # keeps reasoning.
        stream_kwargs: Dict[str, Any] = {}
        if getattr(self.llm, "provider", "") == "openai_compat":
            stream_kwargs["disable_thinking"] = True
        parts: list[str] = []
        async with await self.llm.stream(
            system_prompt=_TITLE_SYSTEM_PROMPT,
            messages=[LLMMessage(
                role="user", content=user_text[:_TITLE_INPUT_MAX_CHARS],
            )],
            tools=[], max_output_tokens=_TITLE_MAX_OUTPUT_TOKENS,
            **stream_kwargs,
        ) as stream:
            # Accumulate the streamed text and fall back to it when the provider
            # leaves result.final_text empty — some models do, which left the
            # title blank and the session stuck on the default (#735 P4). The
            # main turn path uses the same `final_text or joined-parts` fallback.
            async for chunk in stream:
                if getattr(chunk, "kind", None) == "text" and chunk.text:
                    parts.append(chunk.text)
            raw = stream.result.final_text or "".join(parts)
        title = _clean_title(raw or "")
        if title:
            return title
        # Last resort: derive a title from the opening message so a new chat is
        # never left untitled when the model returns nothing usable.
        return _fallback_title(user_text)

    def fallback_title(self, user_text: str) -> Optional[str]:
        """Deterministic title derived from the opening message. The autotitle
        route uses this as a safety net: ``asyncio.wait_for`` cancels
        ``generate_title`` on timeout BEFORE its own in-method fallback runs,
        so without this a slow or erroring model leaves the session stuck on
        the default title."""
        return _fallback_title(user_text)

    def rebuild_llm(
        self, new_client: Any, *,
        fingerprint: Optional[Tuple[str, Optional[str]]] = None,
    ) -> None:
        """Swap in a freshly-built LLM client. Called by the admin settings
        route after a config save so changes take effect without restart.
        Active in-flight streams keep using the old client until they
        finish (Python attribute rebind is atomic per CPython).

        ``fingerprint`` is the DB fingerprint the new client was built
        from. Pass it when the caller already fetched it (the chat-turn
        auto-rebuild path) to avoid a redundant SELECT; omit it from the
        admin PATCH route, in which case we re-fetch so the next chat
        turn on this worker doesn't redundantly rebuild a second time.

        Best-effort closes the old SDK's underlying ``httpx.AsyncClient``
        so repeated saves don't leak connection pools into the process.
        """
        old = self.llm
        self.llm = new_client
        if fingerprint is not None:
            self._config_fingerprint = fingerprint
        elif self._providers_service is not None:
            try:
                self._config_fingerprint = (
                    self._providers_service.get_active_config_fingerprint()
                )
            except Exception:
                # Clearing forces the next turn to re-fetch and rebuild
                # rather than caching a stale or half-read fingerprint.
                self._config_fingerprint = None
        sdk = getattr(old, "_sdk", None)
        close = getattr(sdk, "close", None)
        if close is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Sync context (admin PATCH route, factory init).
            try:
                asyncio.run(close())
            except Exception:
                # A leaked httpx pool is much better than a 500 on save.
                logger.debug(
                    "Failed to close previous LLM SDK; pool may leak",
                    exc_info=True,
                )
            return
        # Async context: anchor the close task in _pending_closes so it
        # isn't GC'd before completion (asyncio holds only weak refs).
        task = loop.create_task(close())
        self._pending_closes.add(task)
        task.add_done_callback(self._pending_closes.discard)

    async def _ensure_current_client(self) -> None:
        """Rebuild ``self.llm`` if the DB-stored config has drifted from
        the fingerprint this worker last built against.

        Hot path (the common case — fingerprint unchanged): one indexed
        SELECT and a tuple compare, no lock. Rebuild path (rare — only
        after an admin save): re-checks the fingerprint under the lock
        so a stampede of concurrent turns observing the same staleness
        collapses to one ``make_llm_client`` call instead of N.

        Build failures (e.g. operator cleared the API key) are logged and
        swallowed — we keep streaming with the previous client. The chat
        turn will fail naturally on the next LLM call if the old client is
        no longer usable, which surfaces the misconfig to the user with the
        provider's own error text instead of a generic 500 here.
        """
        if self._providers_service is None:
            return
        try:
            fp = self._providers_service.get_active_config_fingerprint()
        except Exception:
            logger.exception(
                "Failed to read chat config fingerprint; "
                "continuing with current LLM client.",
            )
            return
        if fp == self._config_fingerprint:
            return
        async with self._rebuild_lock:
            # Re-fetch under the lock so concurrent turns past the fast
            # path collapse to a single rebuild.
            try:
                fp = self._providers_service.get_active_config_fingerprint()
            except Exception:
                logger.exception(
                    "Failed to read chat config fingerprint; "
                    "continuing with current LLM client.",
                )
                return
            if fp == self._config_fingerprint:
                return
            try:
                # Lazy import: avoids the factory → service import cycle
                # and keeps the legacy test path (providers_service=None)
                # from pulling boto3/openai/anthropic into unrelated tests.
                from src.api.blueprints.chat.llm import make_llm_client
                effective = self._providers_service.get_effective_config(fp[0])
                new_client = make_llm_client(effective)
            except Exception:
                logger.exception(
                    "Chat config changed in DB but client rebuild failed; "
                    "keeping previous client. Fix the active provider's "
                    "config under Settings → Chatbot.",
                )
                return
            self.rebuild_llm(new_client, fingerprint=fp)

    async def stream_turn(self, *, session_id: str, user_text: str,
                          user_role: str,
                          user_id: str) -> AsyncIterator[StreamEvent]:
        """Stream one user turn end-to-end.

        Wraps the inner orchestration generator with a wall-clock timeout so
        a hung LLM or tool call cannot stall the request indefinitely.
        """
        start = time.monotonic()
        metrics = _TurnMetrics()
        try:
            await self._ensure_current_client()
            async for e in _with_timeout(
                self._stream_inner(session_id=session_id,
                                   user_text=user_text,
                                   user_role=user_role,
                                   user_id=user_id,
                                   metrics=metrics),
                timeout=self.turn_timeout,
                total_timeout=self.turn_total_timeout,
            ):
                yield e
        except asyncio.TimeoutError:
            metrics.status = "turn_timeout"
            track_chat_rate_limit("turn_timeout")
            yield StreamEvent(
                kind="error",
                payload={"code": "turn_timeout",
                         "message": "Turn exceeded time budget."},
            )
            yield StreamEvent(kind="done")
        except asyncio.CancelledError:
            # Client disconnected / hit Stop. Record it for telemetry, then
            # re-raise so the cancellation keeps unwinding the LLM stream
            # and any in-flight tool instead of being swallowed here.
            metrics.status = "client_cancelled"
            raise
        finally:
            track_chat_turn(
                provider=self.llm.provider,
                model=self.llm.model,
                status=metrics.status,
                duration_seconds=time.monotonic() - start,
                input_tokens=metrics.input_tokens,
                output_tokens=metrics.output_tokens,
            )

    async def _stream_inner(self, *, session_id: str, user_text: str,
                            user_role: str,
                            user_id: str,
                            metrics: _TurnMetrics) -> AsyncIterator[StreamEvent]:
        session = self.repo.load_session(session_id, user_id=user_id)
        if session is None:
            metrics.status = "session_not_found"
            yield StreamEvent(kind="error",
                              payload={"code": "session_not_found"})
            yield StreamEvent(kind="done")
            return

        user_msg = self.repo.append_message(
            session_id, role="user", content=redact_text(user_text),
        )
        yield StreamEvent(kind="message.start",
                          payload={"message_id": user_msg.id, "role": "user"})

        history = self.repo.load_messages(session_id)
        tool_calls_by_message = self.repo.load_tool_calls_by_message(session_id)
        llm_messages = _to_llm_messages(history, tool_calls_by_message)

        from .tool_router import current_chat_role, current_tool_router
        # Read from the injected settings_manager, NOT current_app: this
        # generator is driven from a daemon worker thread (routes.py) that
        # never has a Flask app context, so current_app would raise here and
        # pin routing OFF regardless of the DB flag (PR #803). settings_manager
        # is None only in legacy/unit construction, where routing stays OFF.
        sm = self.settings_manager
        routing_enabled = bool(
            sm.get("chat", "tool_routing_enabled", default=False)
        ) if sm is not None else False
        # Read top_k live per turn (like the enabled flag above) so a Settings →
        # Chatbot → Behavior change applies on the next turn without a process
        # restart. None when routing is OFF / no settings_manager — select()
        # then falls back to the ToolRouter's init-time default.
        routing_top_k = (int(sm.get("chat", "tool_routing_top_k", default=5))
                         if routing_enabled and sm is not None else None)
        current_chat_role.set(user_role)
        # Inject the router for the find_tools handler via contextvar (it runs
        # in this same worker thread / task and cannot reach current_app).
        current_tool_router.set(self.tool_router)
        loaded: Set[str] = set()

        # Flag OFF advertises all_role_tools verbatim — drop find_tools so the
        # OFF set is byte-for-byte pre-feature behavior. The ON path goes
        # through router.select(), which calls for_role() itself and re-adds
        # find_tools, so routing is unaffected by stripping it here.
        all_role_tools = [t for t in self.registry.for_role(user_role)
                          if t.name != "find_tools"]
        system_prompt = build_system_prompt(
            user_role=user_role, tool_routing=routing_enabled)

        assistant_text_parts: List[str] = []
        consecutive_internal_errors = 0
        # (tool_name, canonical_args) -> iteration index at which we first saw it.
        seen_calls: Dict[Tuple[str, str], int] = {}
        # tool_name -> count of calls in this turn (regardless of args).
        call_counts: Dict[str, int] = {}

        iter_tools: List[ToolSpec] = []
        stable_prefix = 0
        _prev_loaded_count = -1
        for iter_idx in range(self.max_iter):
            # Recompute the advertised set only when it can actually change.
            # router.select() depends on (user_text, role, loaded); user_text
            # and role are fixed for the turn and `loaded` only grows (find_tools
            # additions), so reuse the prior selection on iterations that loaded
            # nothing — this avoids re-running the router's embedding/keyword
            # ranking (a model forward pass when an embedder is present) on every
            # loop iteration. _prev_loaded_count=-1 forces the first iteration to
            # compute.
            if len(loaded) != _prev_loaded_count:
                iter_tools = _select_turn_tools(
                    routing_enabled=routing_enabled, router=self.tool_router,
                    all_role_tools=all_role_tools, user_text=user_text,
                    role=user_role, loaded=loaded, top_k=routing_top_k)
                _prev_loaded_count = len(loaded)
                # Where to place the provider's prompt-cache breakpoint within
                # the advertised tools. Routing ON appends a variable retrieved
                # tail that changes per turn/iteration, so the breakpoint must
                # sit after the stable core prefix (router-computed) to keep the
                # cached system+core block identical and cache-hitting. Routing
                # OFF advertises the same full set every iteration, so the whole
                # list is the stable prefix (breakpoint at the end = today's
                # behavior). No-op for providers without client-side caching
                # (openai/qwen/foundry ignore it).
                if routing_enabled and self.tool_router is not None:
                    stable_prefix = self.tool_router.stable_prefix_len(iter_tools)
                else:
                    stable_prefix = len(iter_tools)
            async with await self.llm.stream(
                system_prompt=system_prompt, messages=llm_messages,
                tools=iter_tools, max_output_tokens=self.max_out,
                cache_stable_tool_prefix=stable_prefix,
            ) as stream:
                async for chunk in stream:
                    if chunk.kind == "text" and chunk.text:
                        assistant_text_parts.append(chunk.text)
                        yield StreamEvent(kind="delta",
                                          payload={"text": chunk.text})
                    elif chunk.kind == "tool_call_start":
                        yield StreamEvent(
                            kind="tool.start",
                            payload={"name": chunk.tool_name,
                                     "id": chunk.tool_call_id},
                        )
                # stream.result is populated by __aiter__; safe to capture
                # before __aexit__ — no provider mutates it during teardown.
                result = stream.result

            metrics.input_tokens += int(result.usage.input_tokens or 0)
            metrics.output_tokens += int(result.usage.output_tokens or 0)

            if result.stop_reason == "end_turn":
                metrics.status = "done"
                final_text = result.final_text or "".join(assistant_text_parts)
                m = self.repo.append_message(
                    session_id, role="assistant", content=final_text,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                )
                yield StreamEvent(kind="message.end",
                                  payload={"message_id": m.id,
                                           "role": "assistant"})
                yield StreamEvent(kind="done")
                return

            if result.stop_reason == "tool_use":
                assistant_msg = self.repo.append_message(
                    session_id, role="assistant",
                    content="".join(assistant_text_parts) or None,
                    tool_calls=result.tool_calls,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                )
                assistant_text_parts = []
                llm_messages.append(LLMMessage(
                    role="assistant",
                    content=assistant_msg.content,
                    tool_calls=result.tool_calls,
                ))
                abort_turn = False
                handled_tool_ids: set = set()
                for tc in result.tool_calls:
                    inv = await self.registry.invoke(
                        tc["name"], tc.get("arguments") or {},
                        user_role=user_role,
                    )
                    # Loop-detection advisories layered on the LLM-facing
                    # result (both persist to the audit log too):
                    #   * exact-repeat (same args) — model retrying same call
                    #   * high-frequency (>=3 calls, any args) — model
                    #     permuting args trying to find a working combination
                    # Tool-side work still runs so legitimate status re-polls
                    # aren't masked; these are signal-only.
                    key = (tc["name"], _canonical_args(tc.get("arguments")))
                    prev_iter = seen_calls.get(key)
                    if prev_iter is None:
                        seen_calls[key] = iter_idx
                    call_counts[tc["name"]] = call_counts.get(tc["name"], 0) + 1
                    call_n = call_counts[tc["name"]]

                    result_for_llm = inv.result or ""
                    if prev_iter is not None:
                        # 1-indexed for human-friendly messages.
                        result_for_llm = _REPEAT_CALL_PREFIX.format(
                            prev_iter=prev_iter + 1,
                        ) + result_for_llm
                    elif call_n >= _FREQUENCY_ADVISORY_THRESHOLD:
                        result_for_llm = _FREQUENCY_CALL_PREFIX.format(
                            call_n=call_n, tool_name=tc["name"],
                        ) + result_for_llm
                    self.repo.record_tool_call(
                        message_id=assistant_msg.id, tool_name=tc["name"],
                        arguments=tc.get("arguments") or {},
                        result=result_for_llm, status=inv.status,
                        duration_ms=inv.duration_ms, error=inv.error,
                        artifact=inv.artifact,
                    )
                    tool_end_payload: Dict[str, Any] = {
                        "name": tc["name"], "id": tc["id"],
                        "ok": inv.status == "ok", "status": inv.status,
                    }
                    # Only attach the artifact when the tool produced one.
                    # Keeps the wire shape identical for every text-only
                    # tool call, so existing SSE consumers are unaffected.
                    if inv.artifact is not None:
                        tool_end_payload["artifact"] = inv.artifact
                    yield StreamEvent(
                        kind="tool.end", payload=tool_end_payload,
                    )
                    llm_messages.append(LLMMessage(
                        role="tool", tool_call_id=tc["id"],
                        tool_name=tc["name"], content=result_for_llm,
                    ))
                    handled_tool_ids.add(tc["id"])
                    # Track only internal errors (exceptions). "denied" and
                    # "timeout" aren't retry-worthy internal failures, and
                    # "ok" resets: a single successful tool call means the
                    # LLM made progress.
                    if inv.status == "error":
                        consecutive_internal_errors += 1
                        if (consecutive_internal_errors
                                >= _INTERNAL_ERROR_ABORT_THRESHOLD):
                            abort_turn = True
                            break
                    elif inv.status == "ok":
                        consecutive_internal_errors = 0
                        if (tc["name"] == "find_tools"
                                and routing_enabled
                                and self.tool_router is not None):
                            q = (tc.get("arguments") or {}).get("query", "")
                            for s in self.tool_router.find(q, user_role):
                                loaded.add(s.name)
                if abort_turn:
                    # The break left the remaining parallel tool_use blocks
                    # from this assistant turn unanswered. The summary call
                    # below replays llm_messages to the provider, which rejects
                    # a turn whose tool_use ids aren't all paired with a
                    # tool_result ("Expected toolResult blocks ... for the
                    # following Ids"). Pair each skipped tool_use with a
                    # placeholder result.
                    for tc in result.tool_calls:
                        if tc["id"] in handled_tool_ids:
                            continue
                        llm_messages.append(LLMMessage(
                            role="tool", tool_call_id=tc["id"],
                            tool_name=tc.get("name", ""),
                            content=_ABORTED_TOOL_PLACEHOLDER,
                        ))
                    metrics.status = "tool_errors_exceeded"
                    async for ev in self._emit_final_summary_or_error(
                        cause_code="tool_errors_exceeded",
                        session_id=session_id, system_prompt=system_prompt,
                        llm_messages=llm_messages, metrics=metrics,
                    ):
                        yield ev
                    return
                continue

            if result.stop_reason == "max_tokens":
                # The reply hit the output-token cap mid-stream (common with
                # reasoning models that spend tokens on a hidden <think> block).
                # This is a length limit, NOT a provider misconfiguration, so it
                # must not fall through to the generic llm_error path (which
                # tells the user to check Settings → Chatbot). Keep the partial
                # answer the model already streamed — discarding the turn loses a
                # real answer and the work that produced it — and append a clear
                # truncation notice. Logged at WARNING so ops know to raise the
                # cap if it recurs.
                metrics.status = "max_tokens"
                logger.warning(
                    "Chat reply truncated at max_tokens "
                    "(session=%s, output_tokens=%s)",
                    session_id, result.usage.output_tokens,
                )
                partial = (result.final_text
                           or "".join(assistant_text_parts)).strip()
                # The partial text already streamed as deltas; emit only the
                # notice (separated from the answer) so the live view matches
                # the persisted message exactly.
                notice_delta = f"\n\n{MAX_TOKENS_NOTICE}" if partial \
                    else MAX_TOKENS_NOTICE
                yield StreamEvent(kind="delta", payload={"text": notice_delta})
                content = f"{partial}\n\n{MAX_TOKENS_NOTICE}" if partial \
                    else MAX_TOKENS_NOTICE
                m = self.repo.append_message(
                    session_id, role="assistant", content=content,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    error="max_tokens:truncated",
                )
                yield StreamEvent(kind="message.end",
                                  payload={"message_id": m.id,
                                           "role": "assistant"})
                yield StreamEvent(kind="done")
                return

            metrics.status = "llm_error"
            # Log the raw provider detail server-side so it's never silently
            # dropped (e.g. FoundryResponsesClient on response.failed,
            # BedrockClient on in-stream errors), but keep it out of the
            # client payload: the UI renders the SSE message, so echoing
            # result.error there would leak provider internals to users.
            # The client gets a safe, model-agnostic hint instead (issue #695).
            logger.error(
                "LLM stream error (session=%s, stop_reason=%s): %s",
                session_id, result.stop_reason, result.error or "<none>",
            )
            yield StreamEvent(
                kind="error",
                payload={"code": "llm_error",
                         "message": describe_stream_error(result.error or "")},
            )
            yield StreamEvent(kind="done")
            return

        metrics.status = "tool_iterations_exceeded"
        async for ev in self._emit_final_summary_or_error(
            cause_code="tool_iterations_exceeded",
            session_id=session_id, system_prompt=system_prompt,
            llm_messages=llm_messages, metrics=metrics,
        ):
            yield ev

    async def _emit_final_summary_or_error(
        self, *, cause_code: str, session_id: str,
        system_prompt: str, llm_messages: List[LLMMessage],
        metrics: _TurnMetrics,
    ) -> AsyncIterator[StreamEvent]:
        """After the tool loop gives up, call the LLM once more with tools
        disabled and a system nudge instructing it to summarize what it
        learned. Streams the text as deltas, persists the assistant message
        with ``error=<cause>:recovered`` so the audit trail still records
        the failure, and emits ``message.end``/``done``.

        If the fallback itself fails (provider error, no text produced, outer
        turn timeout fires), emit the original error frame instead so the UI
        can still display *something* about the failure.
        """
        final_system = system_prompt + _SUMMARY_NUDGE.format(cause=cause_code)
        text_parts: List[str] = []
        result = None
        try:
            async with await self.llm.stream(
                system_prompt=final_system, messages=llm_messages,
                tools=[],
                max_output_tokens=min(self.max_out, _SUMMARY_MAX_OUTPUT_TOKENS),
            ) as stream:
                async for chunk in stream:
                    if chunk.kind == "text" and chunk.text:
                        text_parts.append(chunk.text)
                        # Defer emitting deltas to the UI until after we've
                        # scrubbed tool-call XML — otherwise a Qwen3-coder
                        # style leak shows raw <tool_call> tags to the user.
                result = stream.result
        except Exception:
            logger.exception(
                "Final-summary fallback raised after %s", cause_code,
            )

        raw_text = (result.final_text if result else "") or "".join(text_parts)
        # Strip any tool-call XML the model may have emitted as text and
        # require a minimum length so "only XML" doesn't masquerade as a
        # real answer.
        scrubbed = _TOOL_CALL_XML_RE.sub("", raw_text).strip()
        if len(scrubbed) >= _MIN_SUMMARY_CHARS:
            text = scrubbed
            audit_tag = f"{cause_code}:recovered"
        else:
            text = _SUMMARY_FALLBACK_APOLOGY.format(cause=cause_code)
            audit_tag = f"{cause_code}:apology"
            if raw_text and raw_text != scrubbed:
                logger.info(
                    "Summary fallback scrubbed tool-call XML for %s; "
                    "original=%r", cause_code, raw_text[:200],
                )

        # Emit the text the user should see as a single delta, then message.end.
        # One delta keeps the UI simple; re-streaming after scrubbing would
        # require tracking token boundaries across regex substitutions.
        yield StreamEvent(kind="delta", payload={"text": text})

        if result is not None:
            metrics.input_tokens += int(result.usage.input_tokens or 0)
            metrics.output_tokens += int(result.usage.output_tokens or 0)
        in_t = result.usage.input_tokens if result else None
        out_t = result.usage.output_tokens if result else None
        m = self.repo.append_message(
            session_id, role="assistant", content=text,
            input_tokens=in_t, output_tokens=out_t, error=audit_tag,
        )
        yield StreamEvent(kind="message.end",
                          payload={"message_id": m.id, "role": "assistant"})
        yield StreamEvent(kind="done")


def _clean_title(raw: str) -> Optional[str]:
    """Normalise the LLM's title output to plain text within the length cap."""
    text = (raw or "").strip()
    if not text:
        return None
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            text = candidate
            break
    # One pass over combined whitespace/quote/punctuation so a composite suffix
    # like `."` (quote AFTER period) strips cleanly — a punctuation-only
    # rstrip would leave the trailing quote behind.
    text = text.strip(" \t\n\"'`.!?:,;")
    if not text:
        return None
    if len(text) > _TITLE_MAX_CHARS:
        text = text[:_TITLE_MAX_CHARS].rstrip()
    return text


def _fallback_title(user_text: str) -> Optional[str]:
    """Deterministic title derived from the opening message, used only when the
    model returns nothing usable. Keeps a new chat from being stuck on the
    default title (#735 P4)."""
    text = " ".join((user_text or "").split())
    if not text:
        return None
    if len(text) > _TITLE_MAX_CHARS:
        text = text[:_TITLE_MAX_CHARS].rstrip()
    return text


def _to_llm_messages(
    history: List[Any],
    tool_calls_by_message: Optional[Dict[str, List[Any]]] = None,
) -> List[LLMMessage]:
    """Project persisted MessageRows into in-flight LLMMessage shape.

    Tool-result rows live in ``chat_tool_calls``, not ``chat_messages``.
    The Anthropic API requires every ``tool_use`` block to be followed
    immediately by a ``tool_result`` block in the next message — otherwise
    follow-up turns fail with HTTP 400 ``tool_use ids ... without tool_result
    blocks``. So for each persisted assistant message with ``tool_calls``,
    we look up the matching ``chat_tool_calls`` rows (in insertion order)
    and emit a paired tool-role LLMMessage per tool_use id. Pairing is
    positional — insertion order in ``chat_tool_calls`` matches the order
    of ``message.tool_calls`` in the Anthropic request.
    """
    tc_map = tool_calls_by_message or {}
    out: List[LLMMessage] = []
    for m in history:
        if m.role == "user":
            out.append(LLMMessage(role="user", content=m.content or ""))
        elif m.role == "assistant":
            out.append(LLMMessage(role="assistant", content=m.content,
                                  tool_calls=m.tool_calls))
            tool_uses = m.tool_calls or []
            persisted = tc_map.get(m.id, [])
            for i, tu in enumerate(tool_uses):
                if i >= len(persisted):
                    # Defensive: if a tool row is missing (e.g. crash
                    # between persisting tool_calls JSON and recording
                    # the result), emit an empty placeholder so the
                    # LLM call doesn't 400. Anthropic only requires
                    # presence, not content.
                    result_text = ""
                    tool_name = tu.get("name", "")
                else:
                    result_text = persisted[i].result or ""
                    tool_name = persisted[i].tool_name
                out.append(LLMMessage(
                    role="tool",
                    tool_call_id=tu.get("id"),
                    tool_name=tool_name,
                    content=result_text,
                ))
    return out


async def _with_timeout(agen: AsyncIterator[StreamEvent], *,
                        timeout: float,
                        total_timeout: Optional[float] = None
                        ) -> AsyncIterator[StreamEvent]:
    """Wrap an async generator with a per-item timeout AND an optional total
    wall-clock budget for the whole iteration.

    ``timeout`` bounds the gap between two consecutive items (a single hung
    LLM/tool call). ``total_timeout`` (when truthy) bounds the cumulative
    wall-clock across ALL items — a long tool fan-out emits items steadily, so
    the per-item timeout alone never fires while the total runs for minutes,
    overrunning a public gateway's request timeout and 504-ing. When the total
    budget is exhausted we raise :class:`asyncio.TimeoutError`, which the caller
    already turns into a graceful ``turn_timeout`` event.

    :func:`asyncio.wait_for` does not work directly on async generators, so
    each ``__anext__`` call is awaited under it. The wrapped generator is closed
    on every exit path (normal completion, timeout, consumer disconnect, inner
    exception) so the LLM stream's HTTP connection is released promptly instead
    of waiting for GC.
    """
    deadline = (time.monotonic() + total_timeout) if total_timeout else None
    try:
        while True:
            per_item = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                per_item = min(timeout, remaining)
            try:
                item = await asyncio.wait_for(
                    agen.__anext__(), timeout=per_item,
                )
            except StopAsyncIteration:
                return
            yield item
    finally:
        await agen.aclose()
