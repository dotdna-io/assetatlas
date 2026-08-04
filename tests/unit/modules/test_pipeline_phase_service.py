# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for PipelinePhaseService.

Covers the four acceptance-criteria scenarios:
- cold-start (no history)
- normal run (no outliers)
- run with one slow stage
- run with multiple slow stages

Plus run-not-found, missing-generation_run_id, and failed-run handling.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from src.modules.pipeline.pipeline_phase_service import PipelinePhaseService


def _stage_row(stage_key, phase, duration, p95=None, samples=0):
    return {
        "stage_key": stage_key,
        "phase": phase,
        "duration_seconds": duration,
        "p95": p95,
        "samples": samples,
    }


def _meta(status="completed", current_stage=None):
    return {"status": status, "current_stage": current_stage}


def _make_service(run_meta_rows, stage_rows):
    """db.execute_query returns the run-meta rows on the first call and the
    stage rows on the second. Matches the service's two-query design."""
    db = MagicMock()
    db.execute_query.side_effect = [run_meta_rows, stage_rows]
    return PipelinePhaseService(db)


def _slow_stage_keys(phase):
    return [s["stage_key"] for s in phase["sub_stages"] if s["is_slow"]]


def test_returns_none_when_run_not_found():
    svc = _make_service([], [])
    assert svc.get_phases_for_run(uuid4()) is None


def test_completed_run_with_no_history_has_no_outliers():
    svc = _make_service(
        [_meta(status="completed")],
        [
            _stage_row("preprocessing",         "ingest_identify",     1.0),
            _stage_row("scoring",               "enrich_score",        2.0),
            _stage_row("persistence",           "persist_apply_rules", 3.0),
            _stage_row("embedding_update",      "post_process",        4.0),
        ],
    )

    result = svc.get_phases_for_run(uuid4())

    assert result["status"] == "completed"
    by_key = {p["key"]: p for p in result["phases"]}
    assert len(by_key) == 4
    for p in result["phases"]:
        assert _slow_stage_keys(p) == []
        assert p["has_slow_stages"] is False
        assert p["status"] == "success"


def test_warm_up_period_below_five_samples_skips_flagging():
    svc = _make_service(
        [_meta()],
        [_stage_row("scoring", "enrich_score", 100.0, p95=1.0, samples=4)],
    )
    result = svc.get_phases_for_run(uuid4())
    enrich = next(p for p in result["phases"] if p["key"] == "enrich_score")
    assert _slow_stage_keys(enrich) == []
    assert enrich["has_slow_stages"] is False


def test_normal_run_within_baseline_has_no_outliers():
    svc = _make_service(
        [_meta()],
        [_stage_row("scoring", "enrich_score", 1.5, p95=1.0, samples=30)],
    )
    result = svc.get_phases_for_run(uuid4())
    enrich = next(p for p in result["phases"] if p["key"] == "enrich_score")
    assert _slow_stage_keys(enrich) == []


def test_run_with_one_slow_stage():
    svc = _make_service(
        [_meta()],
        [
            _stage_row("preprocessing", "ingest_identify", 0.5, p95=0.6, samples=30),
            _stage_row("scoring",       "enrich_score",    5.0, p95=1.0, samples=30),
        ],
    )
    result = svc.get_phases_for_run(uuid4())

    ingest = next(p for p in result["phases"] if p["key"] == "ingest_identify")
    enrich = next(p for p in result["phases"] if p["key"] == "enrich_score")
    assert _slow_stage_keys(ingest) == []
    assert ingest["has_slow_stages"] is False
    assert _slow_stage_keys(enrich) == ["scoring"]
    assert enrich["has_slow_stages"] is True

    flagged = next(s for s in enrich["sub_stages"] if s["stage_key"] == "scoring")
    assert flagged["duration_seconds"] == 5.0
    assert flagged["p95_seconds"] == 1.0
    assert flagged["is_slow"] is True


def test_run_with_multiple_slow_stages_across_phases():
    svc = _make_service(
        [_meta()],
        [
            _stage_row("id_assignment",   "ingest_identify",     6.0,  p95=1.0, samples=30),
            _stage_row("scoring",         "enrich_score",        10.0, p95=2.0, samples=30),
            _stage_row("persistence",     "persist_apply_rules", 1.0,  p95=2.0, samples=30),
        ],
    )
    result = svc.get_phases_for_run(uuid4())
    by_key = {p["key"]: p for p in result["phases"]}
    assert _slow_stage_keys(by_key["ingest_identify"]) == ["id_assignment"]
    assert _slow_stage_keys(by_key["enrich_score"]) == ["scoring"]
    assert _slow_stage_keys(by_key["persist_apply_rules"]) == []


def test_phase_totals_sum_stage_durations():
    svc = _make_service(
        [_meta()],
        [
            _stage_row("preprocessing",         "ingest_identify", 1.5),
            _stage_row("asset_field_generation", "ingest_identify", 0.5),
            _stage_row("scoring",                "enrich_score",    3.0),
        ],
    )
    result = svc.get_phases_for_run(uuid4())
    by_key = {p["key"]: p for p in result["phases"]}
    assert by_key["ingest_identify"]["duration_seconds"] == 2.0
    assert by_key["enrich_score"]["duration_seconds"] == 3.0


def test_phases_returned_in_pipeline_order():
    svc = _make_service(
        [_meta()],
        [_stage_row("scoring", "enrich_score", 1.0)],
    )
    result = svc.get_phases_for_run(uuid4())
    assert [p["key"] for p in result["phases"]] == [
        "ingest_identify",
        "enrich_score",
        "persist_apply_rules",
        "post_process",
    ]


def test_phases_have_human_label():
    svc = _make_service(
        [_meta()],
        [],
    )
    result = svc.get_phases_for_run(uuid4())
    by_key = {p["key"]: p for p in result["phases"]}
    assert by_key["enrich_score"]["label"] == "Enrich & Score"


def test_sub_stages_carry_full_per_stage_data():
    """Sub_stages must include every stage in the run, with duration and
    is_slow flag — the UI uses this to draw a per-stage bar list inside
    each expandable phase row."""
    svc = _make_service(
        [_meta()],
        [
            _stage_row("preprocessing",         "ingest_identify", 1.5),
            _stage_row("asset_field_generation", "ingest_identify", 0.5,
                       p95=0.1, samples=30),  # slow: 5x p95
        ],
    )
    result = svc.get_phases_for_run(uuid4())
    ingest = next(p for p in result["phases"] if p["key"] == "ingest_identify")
    by_stage = {s["stage_key"]: s for s in ingest["sub_stages"]}

    assert set(by_stage) == {"preprocessing", "asset_field_generation"}
    assert by_stage["preprocessing"]["duration_seconds"] == 1.5
    assert by_stage["preprocessing"]["is_slow"] is False
    assert by_stage["asset_field_generation"]["duration_seconds"] == 0.5
    assert by_stage["asset_field_generation"]["is_slow"] is True


def test_failed_run_marks_partial_phase_failed():
    svc = _make_service(
        [_meta(status="failed", current_stage="Stage 9: ...")],
        [
            # Ingest phase: all 8 expected stages present → success
            _stage_row("cleanup_expired",         "ingest_identify", 0.1),
            _stage_row("preprocessing",           "ingest_identify", 0.1),
            _stage_row("asset_field_generation",  "ingest_identify", 0.1),
            _stage_row("load_mappings",           "ingest_identify", 0.1),
            _stage_row("id_assignment",           "ingest_identify", 0.1),
            _stage_row("conflict_detection",      "ingest_identify", 0.1),
            _stage_row("excluded_records",        "ingest_identify", 0.1),
            _stage_row("expansion",               "ingest_identify", 0.1),
            # Enrich phase: only one stage (enrichment ran, then crashed) → failed
            _stage_row("enrichment",              "enrich_score",    0.1),
        ],
    )
    result = svc.get_phases_for_run(uuid4())
    by_key = {p["key"]: p for p in result["phases"]}
    assert by_key["ingest_identify"]["status"] == "success"
    assert by_key["enrich_score"]["status"] == "failed"
    assert by_key["persist_apply_rules"]["status"] == "not_run"
    assert by_key["post_process"]["status"] == "not_run"
