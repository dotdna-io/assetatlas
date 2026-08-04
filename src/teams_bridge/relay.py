# SPDX-License-Identifier: AGPL-3.0-or-later
"""Map an SSE event stream to ordered StreamActions for the bot to dispatch.

The planner walks the SSE stream from /api/chat/stream and yields one
StreamAction per Teams-side operation. bot.py performs the actual SDK
calls.

Two modes (via `emit_streaming`):

* `True` — yield post_streaming + update_streaming actions as deltas
  arrive. Used when the host channel reliably edits in place.
* `False` — skip post_streaming/update_streaming entirely; coalesce
  the text buffer and only yield post_final + post_card. Used in
  Teams 1:1 today, where update_activity for text edits is unreliable
  (the placeholder bubble persists and updates pile up as separate
  messages). Microsoft's actual Teams streaming protocol uses a
  different streaminfo entity scheme that's a bigger rewrite.

A StreamAction is one of:
  post_streaming    — initial bubble, posted once at message.start
  update_streaming  — edit the bubble's text mid-stream
  post_final        — final body at done/error
  post_card         — separate Adaptive Card message after the final
"""

from __future__ import annotations

import dataclasses
import time
from typing import (AsyncGenerator, AsyncIterable, Callable, List, Literal,
                    Optional)


StreamActionKind = Literal[
    "post_streaming", "update_streaming", "post_final", "post_card",
]


@dataclasses.dataclass
class StreamAction:
    kind: StreamActionKind
    text: str = ""
    message_id: Optional[str] = None
    artifact: Optional[dict] = None


async def plan_stream_actions(
    events: AsyncIterable[dict],
    *,
    throttle_ms: int = 750,
    emit_streaming: bool = True,
    now: Callable[[], float] = lambda: time.monotonic() * 1000,
) -> AsyncGenerator[StreamAction, None]:
    """Yield StreamActions as SSE events arrive.

    With `emit_streaming=True`, yields post_streaming on message.start
    and update_streaming on each throttle-window boundary; delta-text
    in between is coalesced into the running buffer and flushed at the
    next eligible boundary (or in post_final).

    With `emit_streaming=False`, post_streaming/update_streaming are
    suppressed and the buffer is flushed only via post_final — avoiding
    O(N²) joins on long replies when the consumer wouldn't have used
    the intermediate yields anyway.

    `now` is injected so tests can use a deterministic clock.
    """
    buf: List[str] = []
    posted = False
    last_update_at = float("-inf")
    message_id: Optional[str] = None
    pending_artifacts: List[dict] = []

    async for ev in events:
        kind = ev.get("event")
        data = ev.get("data") or {}
        if kind == "message.start":
            if emit_streaming:
                yield StreamAction(
                    "post_streaming", "…",
                    message_id=data.get("message_id"),
                )
                last_update_at = now()
            posted = True
            message_id = data.get("message_id")
        elif kind == "delta" and posted:
            buf.append(data.get("text", ""))
            if emit_streaming:
                current = now()
                if (current - last_update_at) >= throttle_ms:
                    yield StreamAction(
                        "update_streaming", "".join(buf).strip(),
                    )
                    last_update_at = current
        elif kind == "tool.start" and posted:
            tool_name = data.get("name", data.get("tool_name", "?"))
            buf.append(f"\n_Calling tool: {tool_name}…_\n")
        elif kind == "tool.end" and posted:
            artifact = data.get("artifact")
            if artifact:
                pending_artifacts.append(artifact)
        elif kind == "message.end":
            message_id = data.get("message_id") or message_id
        elif kind == "done":
            yield StreamAction(
                "post_final",
                "".join(buf).strip() or "(no reply)",
                message_id=message_id,
            )
            for a in pending_artifacts:
                yield StreamAction("post_card", artifact=a)
        elif kind == "error":
            # Suppress feedback card on error by clearing message_id.
            yield StreamAction(
                "post_final",
                f"Error: {data.get('message', 'stream_failed')}",
                message_id=None,
            )
            return
