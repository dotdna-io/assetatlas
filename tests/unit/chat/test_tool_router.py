# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/chat/test_tool_router.py
import numpy as np

from src.api.blueprints.chat.llm.base import ToolSpec
from src.api.blueprints.chat.tool_router import ToolEmbedder, ToolRouter, CORE_TOOL_NAMES


class _FakeEmbedding:
    """Deterministic 2-D embeddings: query close to the 'integration' axis."""
    is_loaded = True

    def embed_text(self, text):
        return np.array([1.0, 0.0]) if "integration" in text else np.array([0.0, 1.0])

    def embed_texts(self, texts):
        return np.array([[1.0, 0.0] if "integration" in t else [0.0, 1.0]
                         for t in texts])

    def find_most_similar(self, q, cands, top_k=1):
        sims = (cands @ q.reshape(-1, 1)).flatten()
        idx = np.argsort(sims)[::-1][:top_k]
        return [(int(i), float(sims[i])) for i in idx]


def test_embedder_ranks_by_similarity():
    specs = [_spec("get_integration", "integration health"),
             _spec("get_scheduler_status", "scheduler jobs")]
    emb = ToolEmbedder(_FakeEmbedding())
    out = emb.rank("show integration status", specs, limit=1, threshold=0.5)
    assert [s.name for s in out] == ["get_integration"]


def test_embedder_not_ready_returns_none():
    class _Down:
        is_loaded = False
    emb = ToolEmbedder(_Down())
    assert emb.ready is False


class _Registry:
    def __init__(self, specs):
        self._specs = specs

    def for_role(self, role):
        # mirrors the real ToolRegistry.for_role: drops hidden aliases
        # (advertise=False) AND admin_required tools for non-admins
        return [s for s in self._specs
                if s.advertise and (role == "admin" or not s.admin_required)]


def _spec(name, desc="", admin=False):
    return ToolSpec(name=name, description=desc, input_schema={},
                    admin_required=admin)


def _registry():
    core = [_spec(n, f"{n} description") for n in CORE_TOOL_NAMES]
    gated = [
        _spec("get_integration", "integration connector health and status"),
        _spec("get_scheduler_status", "scheduler jobs health"),
        _spec("get_audit_trail", "audit log of admin actions", admin=True),
        _spec("list_eol_assets", "end of life EOL operating systems exposure"),
    ]
    return _Registry(core + gated)


def test_select_always_includes_core_first():
    r = ToolRouter(_registry(), embedder=None, top_k=2)
    out = [s.name for s in r.select("how many servers?", "admin", set())]
    assert out[:len(CORE_TOOL_NAMES)] == list(CORE_TOOL_NAMES)


def test_select_retrieves_relevant_gated_tool_by_keyword():
    r = ToolRouter(_registry(), embedder=None, top_k=2)
    out = [s.name for s in r.select("show integration health", "admin", set())]
    assert "get_integration" in out


def test_select_respects_role_gating():
    r = ToolRouter(_registry(), embedder=None, top_k=5)
    out = [s.name for s in r.select("show me the audit trail", "user", set())]
    assert "get_audit_trail" not in out          # admin-only, user role


def test_select_includes_loaded_tools():
    r = ToolRouter(_registry(), embedder=None, top_k=0)
    out = [s.name for s in r.select("anything", "admin", {"get_scheduler_status"})]
    assert "get_scheduler_status" in out


def test_select_never_surfaces_hidden_aliases():
    specs = [_spec(n) for n in CORE_TOOL_NAMES] + [
        ToolSpec(name="list_assets", description="list assets",
                 input_schema={}, advertise=False)]
    r = ToolRouter(_Registry(specs), embedder=None, top_k=5)
    out = [s.name for s in r.select("list assets", "admin", set())]
    assert "list_assets" not in out


def test_find_returns_matching_gated_specs():
    r = ToolRouter(_registry(), embedder=None)
    names = [s.name for s in r.find("end of life operating systems", "admin")]
    assert "list_eol_assets" in names


def test_stable_prefix_len_counts_leading_core_and_find_tools_only():
    """The stable prefix is the LEADING run of core tools + find_tools that
    select() always emits first in a fixed order. The retrieved gated tail
    (and find_tools-loaded tools) varies per turn, so it must NOT be counted:
    the prompt-cache breakpoint is placed after this prefix and the cached
    system+core block has to be byte-identical across turns/iterations."""
    # find_tools is a real registered tool that select() advertises right after
    # the core set (the shared _registry() stub omits it); include it here so
    # the prefix exercises the core + find_tools boundary like production does.
    core = [_spec(name, f"{name} description") for name in CORE_TOOL_NAMES]
    reg = _Registry(core + [
        _spec("find_tools", "retrieve additional tools by relevance"),
        _spec("get_integration", "integration connector health and status"),
        _spec("get_scheduler_status", "scheduler jobs health"),
    ])
    r = ToolRouter(reg, embedder=None, top_k=2)
    specs = r.select("show integration health", "admin", set())
    n = r.stable_prefix_len(specs)
    stable = set(CORE_TOOL_NAMES) | {"find_tools"}
    # Prefix is all-stable...
    assert all(s.name in stable for s in specs[:n])
    # ...and maximal: the spec right after the prefix is a retrieved gated
    # tool, proving the boundary sits exactly at the core/variable seam.
    assert n < len(specs)
    assert specs[n].name not in stable
    # Concretely: every core tool + find_tools made the prefix.
    assert n == len(CORE_TOOL_NAMES) + 1


def test_stable_prefix_len_stops_at_first_non_stable_tool():
    """A non-stable (retrieved) tool appearing before any core tool ends the
    prefix immediately — the count is the leading run, not a total."""
    r = ToolRouter(_registry(), embedder=None, top_k=0)
    specs = [_spec("get_integration"), _spec("query_assets")]
    assert r.stable_prefix_len(specs) == 0


def test_stable_prefix_len_all_stable_returns_full_length():
    """When every advertised tool is stable (routing-off advertises the whole
    fixed set verbatim each turn), the entire list is the prefix."""
    r = ToolRouter(_registry(), embedder=None, top_k=0)
    specs = [_spec(name) for name in CORE_TOOL_NAMES] + [_spec("find_tools")]
    assert r.stable_prefix_len(specs) == len(specs)
