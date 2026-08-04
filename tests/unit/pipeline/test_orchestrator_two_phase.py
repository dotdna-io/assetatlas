# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for #813 — pipeline transaction isolation.

Two behaviours:

1. EMBEDDING_AUTO_UPDATE is honoured (config/env), so operators get a runtime
   escape hatch for the s22 embedding stage when the description LLM is
   unhealthy — previously the orchestrator hardcoded it to True.

2. Non-critical post-persistence stages (alert evaluation, embedding) run AFTER
   the core asset transaction is committed, so a caught error in them (e.g. a
   broken alert rule query) can neither roll back the committed run nor hold the
   post-TRUNCATE ACCESS EXCLUSIVE lock. The run still reports success.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from src.modules.asset_generation.pipeline.context import PipelineContext


# ---------------------------------------------------------------------------
# Part C — EMBEDDING_AUTO_UPDATE wiring
# ---------------------------------------------------------------------------

def test_resolve_embedding_auto_update_env_false(monkeypatch):
    from src.modules.asset_generation.services.pipeline_orchestrator import (
        _resolve_embedding_auto_update,
    )
    monkeypatch.setenv("EMBEDDING_AUTO_UPDATE", "false")
    with patch("flask.has_app_context", return_value=False):
        assert _resolve_embedding_auto_update() is False


def test_resolve_embedding_auto_update_default_true(monkeypatch):
    from src.modules.asset_generation.services.pipeline_orchestrator import (
        _resolve_embedding_auto_update,
    )
    monkeypatch.delenv("EMBEDDING_AUTO_UPDATE", raising=False)
    with patch("flask.has_app_context", return_value=False):
        assert _resolve_embedding_auto_update() is True


def test_resolve_embedding_auto_update_prefers_app_config():
    from src.modules.asset_generation.services.pipeline_orchestrator import (
        _resolve_embedding_auto_update,
    )
    with patch("flask.has_app_context", return_value=True), patch(
        "flask.current_app", SimpleNamespace(config={"EMBEDDING_AUTO_UPDATE": False})
    ):
        assert _resolve_embedding_auto_update() is False


def test_s22_should_skip_respects_embedding_auto_update_flag():
    # The wired flag must actually control skipping — this is the operator
    # escape hatch the wiring exists to provide.
    from src.modules.asset_generation.pipeline.stages.s22_embedding_update import (
        EmbeddingUpdateStage,
    )
    ctx = PipelineContext(raw_data_df=pd.DataFrame())
    off = EmbeddingUpdateStage(SimpleNamespace(_embedding_auto_update=False))
    on = EmbeddingUpdateStage(SimpleNamespace(_embedding_auto_update=True))
    assert off.should_skip(ctx) is True
    assert on.should_skip(ctx) is False


# ---------------------------------------------------------------------------
# Part A — two-phase run (core committed, non-critical tail isolated)
# ---------------------------------------------------------------------------

class _RecordingStage:
    """Minimal stage stub that records its execution and can raise."""

    def __init__(self, key, events, raises=None):
        self.key = key
        self._events = events
        self._raises = raises

    def execute(self, ctx):
        self._events.append(self.key)
        if self._raises is not None:
            raise self._raises
        return ctx


class _FakeScope:
    """conn_manager stub recording begin/end and commit-vs-rollback intent."""

    def __init__(self, events):
        self._events = events
        self._active = False
        self.end_calls = []

    def is_scope_active(self):
        return self._active

    def begin_scope(self):
        self._active = True
        self._events.append("begin_scope")

    def end_scope(self, error=None, raise_on_aborted=False):
        self._active = False
        self.end_calls.append(error)
        self._events.append("end_scope:rollback" if error is not None else "end_scope:commit")


class _FakeDB:
    def __init__(self, scope):
        self.conn_manager = scope

    def execute_query(self, *args, **kwargs):
        return []  # no run-id collision; telemetry writes no-op


def _bare_orchestrator(scope):
    """Build an orchestrator without the heavy __init__ (stages set by caller)."""
    from src.modules.asset_generation.services.pipeline_orchestrator import (
        PipelineOrchestrator,
    )
    orch = object.__new__(PipelineOrchestrator)
    orch.db = _FakeDB(scope)
    orch.service = SimpleNamespace(_validate_input=lambda df: None)
    orch.posture_evaluation_service = None
    return orch


@patch("src.modules.asset_generation.services.pipeline_orchestrator.record_stage_durations")
def test_post_commit_stage_failure_keeps_committed_run_successful(_rsd):
    # THE regression for a real-world production incident: a broken alert rule (post-commit)
    # must NOT roll back the committed run, and the run still reports success.
    events = []
    scope = _FakeScope(events)
    orch = _bare_orchestrator(scope)
    orch.core_stages = [_RecordingStage("persistence", events)]
    orch.post_commit_stages = [
        _RecordingStage("alert_evaluation", events, raises=RuntimeError("bad alert SQL")),
        _RecordingStage("embedding_update", events),
    ]

    _df, stats = orch.generate_assets(pd.DataFrame({"a": [1]}))

    # Core committed BEFORE the non-critical tail ran.
    assert events.index("end_scope:commit") < events.index("alert_evaluation")
    # Committed exactly once, never rolled back.
    assert scope.end_calls == [None]
    # The broken alert stage did not fail the run.
    assert stats.get("success") is True
    # A later post-commit stage still ran despite the earlier one raising.
    assert "embedding_update" in events


@patch("src.modules.asset_generation.services.pipeline_orchestrator.record_stage_durations")
def test_core_stage_failure_rolls_back_and_skips_post_commit(_rsd):
    # Guard: a CORE stage failure must still roll back the run and never reach
    # the post-commit tail (assets are not durably written).
    events = []
    scope = _FakeScope(events)
    orch = _bare_orchestrator(scope)
    boom = RuntimeError("persistence exploded")
    orch.core_stages = [_RecordingStage("persistence", events, raises=boom)]
    orch.post_commit_stages = [_RecordingStage("alert_evaluation", events)]

    with pytest.raises(RuntimeError, match="persistence exploded"):
        orch.generate_assets(pd.DataFrame({"a": [1]}))

    assert scope.end_calls == [boom]          # rolled back with the error
    assert "alert_evaluation" not in events    # tail never ran


@patch("src.modules.asset_generation.services.pipeline_orchestrator.record_stage_durations")
def test_telemetry_failure_after_commit_does_not_fail_the_run(mock_rsd):
    # #813 review: assets are committed in Phase 1; a failure in the post-commit
    # summary/telemetry (e.g. the record_stage_durations INSERT) must NOT flip
    # the run to failed — a 'failed' status over a committed dataset invites a
    # destructive TRUNCATE rerun.
    mock_rsd.side_effect = RuntimeError("stage-duration INSERT blew up")
    events = []
    scope = _FakeScope(events)
    orch = _bare_orchestrator(scope)
    orch.core_stages = [_RecordingStage("persistence", events)]
    orch.post_commit_stages = [_RecordingStage("embedding_update", events)]

    _df, stats = orch.generate_assets(pd.DataFrame({"a": [1]}))

    assert scope.end_calls == [None]       # committed, not rolled back
    assert stats.get("success") is True    # telemetry failure did not fail the run


@patch("src.modules.asset_generation.services.pipeline_orchestrator.record_stage_durations")
def test_post_commit_stage_error_merges_into_stats_without_clobbering(_rsd):
    # A raising post-commit stage that already recorded partial telemetry must
    # keep it — the error is merged under the stage key, not overwritten.
    events = []
    scope = _FakeScope(events)
    orch = _bare_orchestrator(scope)

    class _StatWritingThenRaising:
        key = "alert_evaluation"

        def execute(self, ctx):
            ctx.stats.setdefault("stages", {})["alert_evaluation"] = {"alerts_created": 3}
            raise RuntimeError("boom after recording stats")

    orch.core_stages = [_RecordingStage("persistence", events)]
    orch.post_commit_stages = [_StatWritingThenRaising()]

    _df, stats = orch.generate_assets(pd.DataFrame({"a": [1]}))

    entry = stats["stages"]["alert_evaluation"]
    assert entry["alerts_created"] == 3            # preserved, not clobbered
    assert entry["error"] == "boom after recording stats"


def test_post_commit_stages_are_classified_by_the_stage_flag():
    # The two-phase safety relies on classification living ON the stage, not a
    # hardcoded orchestrator list — so a new non-critical stage can't be
    # silently pulled back into the atomic transaction (#813 review).
    from src.modules.asset_generation.pipeline.stages.s20_alert_evaluation import (
        AlertEvaluationStage,
    )
    from src.modules.asset_generation.pipeline.stages.s22_embedding_update import (
        EmbeddingUpdateStage,
    )
    from src.modules.asset_generation.pipeline.stages.s16_persistence import (
        PersistenceStage,
    )
    assert AlertEvaluationStage.post_commit is True
    assert EmbeddingUpdateStage.post_commit is True
    assert PersistenceStage.post_commit is False
