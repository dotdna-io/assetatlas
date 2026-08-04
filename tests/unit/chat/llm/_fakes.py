# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared async SDK fakes used by OpenAI + OpenAI-compat client tests.

The real ``AsyncOpenAI`` SDK yields streaming chunks through
``chat.completions.create(..., stream=True)``. Tests want deterministic
control over those chunks plus the ability to assert on the arguments
that reached ``create``. These helpers provide a minimal compatible facade
so each test file doesn't need its own copy.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional


class ScriptedAsyncStream:
    """Async iterator + ``aclose`` that replays a list of scripted chunks."""

    def __init__(self, events: List[Any]) -> None:
        self._events = list(events)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._i]
        self._i += 1
        return ev

    async def aclose(self) -> None:
        pass


class FakeSDK:
    """Drop-in stand-in for ``AsyncOpenAI``.

    Records the last kwargs passed to ``chat.completions.create`` in
    ``last_call`` so tests can assert on the params sent to the server
    (tools passed, system prompt augmented, etc.).
    """

    def __init__(self, events: List[Any]) -> None:
        self._events = events
        self.last_call: Optional[Any] = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(
            create=self._create,
        ))

    async def _create(self, **kwargs: Any) -> ScriptedAsyncStream:
        self.last_call = kwargs
        return ScriptedAsyncStream(self._events)


def chunk(*, content=None, tool_calls=None, finish_reason=None, usage=None):
    """Build one streaming chunk matching the openai-python SDK shape."""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason, index=0)
    return SimpleNamespace(choices=[choice], usage=usage)


def tool_call(*, index, id=None, name=None, args=None):
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=id, function=fn)


def final_usage(prompt: int = 0, completion: int = 0, cached: int = 0):
    """Usage-only end-of-stream chunk (include_usage=True convention)."""
    details = SimpleNamespace(cached_tokens=cached)
    usage = SimpleNamespace(prompt_tokens=prompt,
                            completion_tokens=completion,
                            prompt_tokens_details=details)
    # choices=[] signals a usage-only chunk per openai-python contract.
    return SimpleNamespace(choices=[], usage=usage)
