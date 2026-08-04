# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chat Tool Registry

Holds the active set of tool descriptors plus their async handlers, filters
them by user role for advertisement to the LLM, and dispatches invocations
with a per-tool timeout and byte-bounded result truncation. Handlers are
registered by tool_bindings.py and consumed by the orchestration service.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from src.api.utils.metrics import track_chat_tool_call
from src.auth.middleware.rbac import has_required_role

from .artifacts import validate_envelope
from .llm.base import ToolSpec
from .models import ToolCallStatus
from .redaction import truncate_tool_result

logger = logging.getLogger(__name__)


# Handlers may return plain text or a ``(text, artifact)`` tuple.
ToolResult = Union[str, Tuple[str, Optional[Dict[str, Any]]]]
ToolHandler = Callable[..., Awaitable[ToolResult]]


def _check_args_against_schema(
    args: Dict[str, Any], schema: Dict[str, Any], tool_name: str,
) -> Optional[str]:
    """Validate LLM-supplied args against a tool's declared input_schema.

    Returns None if the args are acceptable, or a human-readable error
    string (to be shown to the LLM verbatim) if not. Checks that are cheap
    and actionable:

    - Unknown keys (the model often fabricates parameter names — telling it
      the valid set is usually enough to unblock the next turn).
    - Required keys missing.

    We deliberately skip deep JSON-Schema type checking — the underlying
    tool impls already normalize/coerce types (strings vs ints, JSON-in-
    string filters, etc.) and a schema-strict gate would reject calls that
    actually work.
    """
    if not isinstance(args, dict):
        return (f"Tool {tool_name!r} expected an object for its arguments; "
                f"got {type(args).__name__}.")

    props = (schema or {}).get("properties") or {}
    valid_keys = set(props.keys())

    # Unknown keys (only if the schema declares any properties — otherwise
    # we have no basis to judge and should stay permissive).
    if valid_keys:
        unknown = sorted(k for k in args if k not in valid_keys)
        if unknown:
            return (
                f"Tool {tool_name!r} was called with unknown parameter(s) "
                f"{unknown!r}. Valid parameters: {sorted(valid_keys)!r}. "
                f"Retry with the correct name(s)."
            )

    # Required keys present?
    required = schema.get("required") if isinstance(schema, dict) else None
    if isinstance(required, list):
        missing = [k for k in required if k not in args]
        if missing:
            return (
                f"Tool {tool_name!r} is missing required parameter(s) "
                f"{missing!r}. Retry supplying them."
            )

    # Enum checks. Tool impls often silently coerce unknown values to a
    # default (e.g. time_range="90d" → 7d in get_alerts_impl), which turns
    # "last 90 days" into "last 7 days" without the model knowing. When a
    # schema declares an enum, enforce it — the model gets a clean error
    # listing valid options.
    for key, val in args.items():
        prop = props.get(key) if isinstance(props, dict) else None
        if not isinstance(prop, dict):
            continue
        allowed = prop.get("enum")
        if isinstance(allowed, list) and val not in allowed and val is not None:
            return (
                f"Tool {tool_name!r} parameter {key!r}={val!r} is not one "
                f"of the allowed values {allowed!r}. Retry with a valid value."
            )

    return None


@dataclass
class ToolInvocation:
    """Result of a single ToolRegistry.invoke() call."""
    status: ToolCallStatus
    result: str                 # text (possibly truncated)
    duration_ms: int
    error: Optional[str] = None
    # Optional frontend-rendered payload (see artifacts.validate_envelope).
    # None when the handler returned text only or validation rejected it.
    artifact: Optional[Dict[str, Any]] = None


class ToolRegistry:
    """Registry of chat tools with role-gating, timeout, and truncation."""

    def __init__(self, *, max_result_bytes: int = 50_000, per_tool_timeout: float = 20.0):
        self._tools: Dict[str, Tuple[ToolHandler, ToolSpec]] = {}
        self._max_bytes = max_result_bytes
        self._timeout = per_tool_timeout

    def register(self, name: str, handler: ToolHandler, description: str,
                 input_schema: Dict[str, Any], *, admin_required: bool = False,
                 advertise: bool = True) -> None:
        """Register a tool handler under ``name`` with its public ToolSpec."""
        spec = ToolSpec(name=name, description=description,
                        input_schema=input_schema, admin_required=admin_required,
                        advertise=advertise)
        self._tools[name] = (handler, spec)

    def hide(self, *names: str) -> None:
        """Mark tools invocable-but-not-advertised (e.g. deprecated aliases).

        Their schemas stop shipping to the LLM (saving input tokens every
        turn), but ``invoke`` still dispatches them — so a model that calls an
        old name out of habit keeps working instead of erroring and burning an
        extra retry iteration. Unknown names are ignored (a shim may not be
        registered when its backing service is absent)."""
        for name in names:
            pair = self._tools.get(name)
            if pair is not None:
                pair[1].advertise = False

    def is_registered(self, name: str) -> bool:
        """True if a handler exists for ``name`` (invocable), regardless of
        whether it is advertised to the LLM."""
        return name in self._tools

    def for_role(self, user_role: str) -> List[ToolSpec]:
        """Return the ToolSpecs advertised to ``user_role``.

        Excludes admin-gated tools for non-admins and any tool marked
        ``advertise=False`` (deprecated aliases stay invocable via ``invoke``
        but are not sent to the LLM)."""
        is_admin = has_required_role(user_role, "admin")
        return [s for (_, s) in self._tools.values()
                if s.advertise and (is_admin or not s.admin_required)]

    async def invoke(self, name: str, args: Dict[str, Any], *, user_role: str) -> ToolInvocation:
        """Invoke ``name`` with ``args``; enforce role gate, timeout, and truncation."""
        start = time.monotonic()
        pair = self._tools.get(name)
        if not pair:
            dt = time.monotonic() - start
            track_chat_tool_call(name, "error", dt)
            return ToolInvocation(status="error",
                                  result=f"Tool {name!r} is not available.",
                                  duration_ms=int(dt * 1000),
                                  error=f"unknown tool {name}")
        handler, spec = pair
        if spec.admin_required and not has_required_role(user_role, "admin"):
            dt = time.monotonic() - start
            track_chat_tool_call(name, "denied", dt)
            return ToolInvocation(status="denied",
                                  result=f"Tool {name!r} requires admin role.",
                                  duration_ms=int(dt * 1000),
                                  error="role gate")
        # Validate args against the advertised schema. Without this, a bogus
        # kwarg from the LLM ("rowparameter=row_field", "searchfilters", …)
        # crashes the handler with a raw TypeError that leaves the model
        # with no idea which key is wrong. Up front, we can tell it.
        schema_error = _check_args_against_schema(args, spec.input_schema, name)
        if schema_error is not None:
            dt = time.monotonic() - start
            logger.info("Tool %r schema rejection: %s (args=%r)",
                        name, schema_error, args)
            track_chat_tool_call(name, "error", dt)
            return ToolInvocation(status="error",
                                  result=schema_error,
                                  duration_ms=int(dt * 1000),
                                  error="schema_violation")
        try:
            raw = await asyncio.wait_for(handler(**args), timeout=self._timeout)
            if isinstance(raw, tuple):
                if len(raw) != 2:
                    logger.warning(
                        "Tool %r returned tuple of length %d; expected 2. "
                        "Dropping extras.", name, len(raw),
                    )
                    text = raw[0] if raw else ""
                    artifact_raw: Any = None
                else:
                    text, artifact_raw = raw
            else:
                text = raw
                artifact_raw = None
            artifact = validate_envelope(artifact_raw, tool_name=name)
            result = truncate_tool_result(text or "", max_bytes=self._max_bytes)
            dt = time.monotonic() - start
            track_chat_tool_call(name, "ok", dt)
            return ToolInvocation(status="ok", result=result,
                                  duration_ms=int(dt * 1000),
                                  artifact=artifact)
        except asyncio.TimeoutError:
            dt = time.monotonic() - start
            logger.warning("Tool %r timed out after %.1fs (args=%r)",
                           name, self._timeout, args)
            track_chat_tool_call(name, "timeout", dt)
            return ToolInvocation(status="timeout",
                                  result=f"Tool {name!r} timed out.",
                                  duration_ms=int(dt * 1000),
                                  error="timeout")
        except (ValueError, KeyError, LookupError) as e:
            # Validation-style errors. The message is conventionally safe to
            # share (field names, unknown operators, missing IDs) and — more
            # importantly — teaches the model how to fix its next call. Keep
            # a length cap so a pathological message can't blow the context.
            dt = time.monotonic() - start
            message = str(e)[:500] or e.__class__.__name__
            logger.info("Tool %r validation error: %s (args=%r)",
                        name, message, args)
            track_chat_tool_call(name, "error", dt)
            return ToolInvocation(status="error",
                                  result=f"Tool {name!r} error: {message}",
                                  duration_ms=int(dt * 1000),
                                  error=str(e))
        except Exception as e:
            # Unknown errors. Full traceback to the server log for diagnosis.
            # The LLM-facing result keeps only the exception class name so
            # potentially sensitive detail (DB internals, paths, stack frames)
            # doesn't leak into a shared transcript. The taxonomy layer (see
            # ChatService) decides retry vs. abort based on the status, not
            # the detail.
            dt = time.monotonic() - start
            logger.exception("Tool %r raised %s (args=%r)",
                             name, e.__class__.__name__, args)
            track_chat_tool_call(name, "error", dt)
            return ToolInvocation(status="error",
                                  result=f"Tool {name!r} failed: {e.__class__.__name__}",
                                  duration_ms=int(dt * 1000),
                                  error=str(e))
