# SPDX-License-Identifier: AGPL-3.0-or-later
"""Selects which ToolSpecs to advertise to the LLM per turn.

Core tools are always advertised; the rest ("gated") are retrieved by relevance
to the user's message (embedding when available, keyword otherwise), plus any
tools loaded mid-turn via find_tools. The registry remains the source of truth
for invocation — this only narrows the ADVERTISED set."""
from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import List, Optional, Set

# Per-request role for the find_tools handler. Set in service._stream_inner so
# concurrent requests with different roles never race on a shared attribute.
current_chat_role: ContextVar[str] = ContextVar("current_chat_role",
                                                 default="user")

# Per-request ToolRouter for the find_tools handler. Injected via this
# contextvar (NOT current_app) because the turn — and every tool handler it
# invokes — runs in a daemon worker thread with no Flask app context, where
# current_app raises (the same trap PR #803 fixed for the routing flag). Set in
# service._stream_inner alongside current_chat_role; defaults to None so any
# path that never set it degrades to "discovery unavailable" instead of raising.
current_tool_router: ContextVar[Optional["ToolRouter"]] = ContextVar(
    "current_tool_router", default=None)

from .llm.base import ToolSpec

logger = logging.getLogger(__name__)


class ToolEmbedder:
    """Ranks ToolSpecs against a query via the existing EmbeddingService.

    Tool-description vectors are cached (the set is fixed per process). Any
    failure returns None from rank() so ToolRouter falls back to keyword."""

    def __init__(self, embedding_service):
        self._svc = embedding_service
        self._matrix = None
        self._names: List[str] = []

    @property
    def ready(self) -> bool:
        return bool(self._svc) and getattr(self._svc, "is_loaded", False)

    def _ensure_matrix(self, specs: List[ToolSpec]):
        names = [s.name for s in specs]
        if names == self._names and self._matrix is not None:
            return
        docs = [f"{s.name}. {s.description}" for s in specs]
        self._matrix = self._svc.embed_texts(docs)
        self._names = names

    def rank(self, query: str, specs: List[ToolSpec], limit: int,
             threshold: float) -> Optional[List[ToolSpec]]:
        try:
            if not specs:
                return []
            self._ensure_matrix(specs)
            qv = self._svc.embed_text(query)
            pairs = self._svc.find_most_similar(qv, self._matrix, top_k=limit)
            by_name = {s.name: s for s in specs}
            return [by_name[self._names[i]] for i, score in pairs
                    if score >= threshold]
        except Exception as exc:                       # degrade, never 500
            logger.warning("ToolEmbedder.rank failed, using keyword: %s", exc)
            return None


# The tight core (always advertised). Resolves the bulk of end-user questions.
CORE_TOOL_NAMES = (
    "query_assets", "describe_catalog", "semantic_suggest", "get_asset",
    "run_chart", "get_alerts", "get_dashboard", "export_assets",
)

# find_tools is advertised alongside core but lives in the registry like any
# tool; the router never needs to special-case its NAME for selection.

# Names that select() always emits first, in a fixed order: the core tools plus
# find_tools. These form the stable prompt-cache prefix (see stable_prefix_len);
# everything select() appends after them (loaded + retrieved gated tools) varies
# per turn.
_STABLE_TOOL_NAMES = frozenset(CORE_TOOL_NAMES) | {"find_tools"}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> Set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


class ToolRouter:
    def __init__(self, registry, embedder=None, top_k: int = 5,
                 threshold: float = 0.0):
        self._registry = registry
        self._embedder = embedder      # ToolEmbedder | None (Task 4)
        self._top_k = top_k
        self._threshold = threshold

    # -- candidate sets -------------------------------------------------
    def _advertised_specs(self, role: str) -> List[ToolSpec]:
        # registry.for_role is the single filter gate: it drops admin-gated
        # tools for non-admins AND hidden aliases (advertise=False).
        return self._registry.for_role(role)

    def _gated(self, role: str) -> List[ToolSpec]:
        core = set(CORE_TOOL_NAMES)
        return [s for s in self._advertised_specs(role)
                if s.name not in core and s.name != "find_tools"]

    # -- scoring --------------------------------------------------------
    def _rank(self, query: str, specs: List[ToolSpec],
              limit: int) -> List[ToolSpec]:
        if not specs or limit <= 0:
            return []
        if self._embedder is not None and self._embedder.ready:
            ranked = self._embedder.rank(query, specs, limit, self._threshold)
            if ranked is not None:
                return ranked
        # keyword fallback: Jaccard-ish overlap of query tokens vs name+desc
        q = _tokens(query)
        scored = []
        for s in specs:
            doc = _tokens(f"{s.name} {s.description}")
            overlap = len(q & doc)
            if overlap > 0:
                scored.append((overlap, s))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [s for _, s in scored[:limit]]

    # -- public ---------------------------------------------------------
    def select(self, user_text: str, role: str, loaded: Set[str],
               top_k: Optional[int] = None) -> List[ToolSpec]:
        # top_k is the retrieval breadth; callers pass it live per turn so a
        # settings change applies without a restart. None => init-time default.
        limit = self._top_k if top_k is None else top_k
        by_name = {s.name: s for s in self._advertised_specs(role)}
        # 1) core, in declared order (stable prefix for caching)
        out: List[ToolSpec] = [by_name[n] for n in CORE_TOOL_NAMES
                               if n in by_name]
        if "find_tools" in by_name:
            out.append(by_name["find_tools"])
        chosen = {s.name for s in out}
        # 2) loaded (from find_tools earlier this turn)
        for n in loaded:
            if n in by_name and n not in chosen:
                out.append(by_name[n])
                chosen.add(n)
        # 3) retrieved gated
        for s in self._rank(user_text, self._gated(role), limit):
            if s.name not in chosen:
                out.append(s)
                chosen.add(s.name)
        return out

    def stable_prefix_len(self, specs: List[ToolSpec]) -> int:
        """Number of LEADING specs in ``specs`` that are stable across turns.

        ``select()`` always emits the core tools + find_tools first, in a fixed
        order; the loaded + retrieved gated tools it appends afterwards vary per
        turn. Counting the leading stable-named run gives the boundary at which
        the prompt-cache breakpoint must sit so the cached system+core block is
        byte-identical across turns/iterations (the retrieved tail then re-reads
        cheaply without busting the cache). Stops at the first non-stable spec —
        this is a leading run, not a total count."""
        n = 0
        for s in specs:
            if s.name not in _STABLE_TOOL_NAMES:
                break
            n += 1
        return n

    def find(self, query: str, role: str, limit: int = 8) -> List[ToolSpec]:
        return self._rank(query, self._gated(role), limit)
