# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the pipeline stage-duration persistence helper."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from src.modules.pipeline.stage_duration_recorder import record_stage_durations


def _stats(stages: dict) -> dict:
    """Build the minimal stats dict shape the orchestrator emits."""
    return {"stages": stages}


def test_records_one_row_per_timed_stage():
    db = MagicMock()
    run_id = uuid4()
    started_at = datetime(2026, 5, 4, 10, 0, 0, tzinfo=timezone.utc)
    stats = _stats({
        "preprocessing": {"duration": 1.23, "label": "Preprocessing"},
        "scoring":       {"duration": 4.56, "label": "Scoring"},
        "persistence":   {"duration": 0.78, "label": "Persistence"},
    })

    record_stage_durations(db, run_id, started_at, stats)

    assert db.execute_query.call_count == 3


def test_skips_stages_without_duration():
    """Stages that were skipped (or errored before recording duration) have
    no `duration` key. They must not be persisted as zero-duration rows."""
    db = MagicMock()
    stats = _stats({
        "preprocessing": {"duration": 1.0},
        "embedding_update": {"skipped": True},
        "broken": {},
    })

    record_stage_durations(db, uuid4(), datetime.now(timezone.utc), stats)

    assert db.execute_query.call_count == 1


def test_writes_phase_alongside_stage():
    db = MagicMock()
    run_id = uuid4()
    started_at = datetime(2026, 5, 4, 10, 0, 0, tzinfo=timezone.utc)
    stats = _stats({"scoring": {"duration": 2.0}})

    record_stage_durations(db, run_id, started_at, stats)

    args, kwargs = db.execute_query.call_args
    # Positional %s + tuple to match pipeline_service.py.
    params = args[1] if len(args) > 1 else kwargs.get("params")
    assert params is not None
    # Order is fixed in the helper — assert the set of values is correct
    # rather than positional indices, so reordering the SQL columns later
    # doesn't break this test silently.
    values = list(params)
    assert str(run_id) in [str(v) for v in values]
    assert "scoring" in values
    assert "enrich_score" in values
    assert started_at in values
    assert any(isinstance(v, float) and v == 2.0 for v in values)


def test_unknown_stage_is_skipped_with_warning(caplog):
    """A stage key that's not in STAGE_TO_PHASE means someone added a new
    stage but forgot the phase mapping — we log a warning and skip the row
    rather than crash the pipeline."""
    db = MagicMock()
    stats = _stats({
        "scoring": {"duration": 1.0},
        "brand_new_stage_no_phase_yet": {"duration": 2.0},
    })

    import logging
    with caplog.at_level(logging.WARNING):
        record_stage_durations(db, uuid4(), datetime.now(timezone.utc), stats)

    assert db.execute_query.call_count == 1
    assert any(
        "brand_new_stage_no_phase_yet" in rec.message
        for rec in caplog.records
    )


def test_db_failure_does_not_raise():
    """A failure inserting into pipeline_stage_durations must never kill
    the pipeline run. The function should swallow the exception and log it."""
    db = MagicMock()
    db.execute_query.side_effect = RuntimeError("network blip")
    stats = _stats({"scoring": {"duration": 1.0}})

    # Must not raise.
    record_stage_durations(db, uuid4(), datetime.now(timezone.utc), stats)


def test_no_stages_no_calls():
    db = MagicMock()
    record_stage_durations(db, uuid4(), datetime.now(timezone.utc), _stats({}))
    assert db.execute_query.call_count == 0
