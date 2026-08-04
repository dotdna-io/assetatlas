# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the list_pipeline_runs MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.pipeline_runs import _parse_date, list_pipeline_runs_impl


def _run(**overrides):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "triggered_by": "manual",
        "status": "completed",
        "started_at": "2026-04-20T10:00:00+00:00",
        "completed_at": "2026-04-20T10:05:00+00:00",
        "duration_seconds": 300.0,
        "error_message": None,
        "created_at": "2026-04-20T10:00:00+00:00",
        "total_assets": 1000,
        "assets_created": 50,
        "assets_updated": 200,
        "assets_removed": 5,
        "batches_processed": 3,
    }
    base.update(overrides)
    return base


def test_list_form_renders_all_runs():
    svc = MagicMock()
    runs = [
        _run(id="11111111-1111-1111-1111-111111111111", status="completed"),
        _run(id="22222222-2222-2222-2222-222222222222", status="running"),
        _run(id="33333333-3333-3333-3333-333333333333", status="failed",
             error_message="boom"),
    ]
    svc.get_runs.return_value = {
        "runs": runs, "total": 3, "page": 1, "per_page": 20, "pages": 1,
    }

    out = list_pipeline_runs_impl(svc)

    assert "# Pipeline runs" in out
    assert "11111111-1111-1111-1111-111111111111" in out
    assert "22222222-2222-2222-2222-222222222222" in out
    assert "33333333-3333-3333-3333-333333333333" in out
    assert "completed" in out
    assert "running" in out
    assert "failed" in out
    assert "boom" in out


def test_list_form_empty():
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [], "total": 0, "page": 1, "per_page": 20, "pages": 0,
    }
    out = list_pipeline_runs_impl(svc)
    assert "No pipeline runs recorded." in out


def test_detail_form_found():
    svc = MagicMock()
    run_id = "11111111-1111-1111-1111-111111111111"
    svc.get_run.return_value = {
        "id": run_id,
        "triggered_by": "schedule",
        "status": "completed",
        "current_stage": None,
        "started_at": "2026-04-20T10:00:00+00:00",
        "completed_at": "2026-04-20T10:05:00+00:00",
        "duration_seconds": 300.0,
        "error_message": None,
        "stats": {
            "total_assets": 1000,
            "total_assets_created": 50,
            "total_assets_updated": 200,
            "removed_assets": 5,
            "merged_assets": 2,
            "batches_processed": 3,
        },
    }

    out = list_pipeline_runs_impl(svc, run_id=run_id)

    assert f"# Pipeline run {run_id}" in out
    assert "completed" in out
    assert "schedule" in out
    assert "## Asset stats" in out
    assert "total assets: 1000" in out
    assert "created: 50" in out
    assert "returned: 0" in out  # legacy run (no chronicle_events) → 0 returned
    assert "updated: 200" in out
    assert "removed: 5" in out
    assert "merged: 2" in out
    assert "batches: 3" in out


def test_detail_form_not_found():
    svc = MagicMock()
    svc.get_run.return_value = None
    run_id = "11111111-1111-1111-1111-111111111111"

    out = list_pipeline_runs_impl(svc, run_id=run_id)

    assert "not found" in out.lower()
    assert run_id in out


def test_detail_form_invalid_uuid_does_not_call_service():
    svc = MagicMock()

    out = list_pipeline_runs_impl(svc, run_id="not-a-uuid")

    assert "Invalid pipeline run ID" in out
    svc.get_run.assert_not_called()


def test_none_service_returns_error_string():
    out = list_pipeline_runs_impl(None)
    assert "not available" in out.lower()


# --- merged current status (replaces get_pipeline_status) -------------------

def test_list_merges_current_running_status():
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [_run()], "total": 1, "page": 1, "per_page": 20, "pages": 1,
    }
    svc.get_current_status.return_value = {
        "status": "running",
        "run_id": "abc",
        "current_stage": "aggregation",
        "elapsed_seconds": 42.0,
    }
    out = list_pipeline_runs_impl(svc)
    # The single tool now answers "what's running now" AND "what ran before".
    assert "# Pipeline status" in out
    assert "running" in out
    assert "aggregation" in out
    assert "# Pipeline runs" in out


def test_list_merges_idle_status_with_last_run():
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [_run()], "total": 1, "page": 1, "per_page": 20, "pages": 1,
    }
    svc.get_current_status.return_value = {
        "status": "idle",
        "last_run": {"run_id": "last-1", "status": "completed",
                     "completed_at": "2026-04-20T10:05:00+00:00"},
    }
    out = list_pipeline_runs_impl(svc)
    assert "state: idle" in out
    assert "last-1" in out


# --- loud validation (never silently widen) --------------------------------

def test_unknown_status_is_loud_error_not_silent_widen():
    # A dropped status filter would return *every* run (wider than asked) —
    # a correctness + over-disclosure bug. So it must error, not widen.
    svc = MagicMock()
    out = list_pipeline_runs_impl(svc, status="donezo")
    assert "Unknown status" in out
    assert "donezo" in out
    svc.get_runs.assert_not_called()


def test_unknown_sort_is_loud_error():
    svc = MagicMock()
    out = list_pipeline_runs_impl(svc, sort="sideways")
    assert "Unknown sort" in out
    svc.get_runs.assert_not_called()


def test_bad_date_is_loud_error():
    svc = MagicMock()
    out = list_pipeline_runs_impl(svc, start_date="not-a-date")
    assert "Invalid start_date" in out
    svc.get_runs.assert_not_called()


def test_filters_threaded_into_service():
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [], "total": 0, "page": 1, "per_page": 20, "pages": 0,
    }
    list_pipeline_runs_impl(svc, status="failed", sort="duration",
                            sort_dir="asc", start_date="2026-04-01")
    kwargs = svc.get_runs.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["sort"] == "duration"
    assert kwargs["sort_dir"] == "asc"
    assert kwargs["start_date"] is not None  # parsed to a datetime


def test_empty_with_filters_explains_filters():
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [], "total": 0, "page": 1, "per_page": 20, "pages": 0,
    }
    out = list_pipeline_runs_impl(svc, status="failed")
    # Don't imply "no data exists" when a filter caused the empty result.
    assert "filter" in out.lower()


def test_paging_overflow_signal():
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [_run()], "total": 45, "page": 1, "per_page": 20, "pages": 3,
    }
    out = list_pipeline_runs_impl(svc, per_page=20)
    assert "page 1 of 3" in out
    assert "total 45" in out
    assert "more run(s) not shown" in out


# --- detail enrichment ------------------------------------------------------

def test_detail_includes_batch_stage_chronicle():
    svc = MagicMock()
    run_id = "11111111-1111-1111-1111-111111111111"
    svc.get_run.return_value = {
        "id": run_id, "triggered_by": "manual", "status": "completed",
        "current_stage": None, "started_at": "2026-04-20T10:00:00+00:00",
        "completed_at": "2026-04-20T10:05:00+00:00", "duration_seconds": 300.0,
        "error_message": None,
        "stats": {
            "total_assets": 10,
            "chronicle_events": {"created": 3, "updated": 7},
            "stage_timings": [{"name": "Aggregation", "duration": 12.5}],
            "batch_results": [
                {"batch_id": "b1", "success": True, "input_records": 100,
                 "integration_name": "Jamf", "unique_assets": 90,
                 "error": None},
            ],
        },
    }
    out = list_pipeline_runs_impl(svc, run_id=run_id)
    assert "## Chronicle events" in out and "created: 3" in out
    assert "## Stage timings" in out and "Aggregation" in out
    assert "## Batch results" in out and "Jamf" in out


def test_detail_splits_created_into_new_and_returned():
    """Asset-stats 'created' shows new-only (chronicle asset_created) and a
    'returned' line carries asset_readded, matching the UI Run History split
    (PR #794). The combined total_assets_created is not the headline count."""
    svc = MagicMock()
    run_id = "11111111-1111-1111-1111-111111111111"
    svc.get_run.return_value = {
        "id": run_id, "triggered_by": "schedule", "status": "completed",
        "current_stage": None, "started_at": "2026-04-20T10:00:00+00:00",
        "completed_at": "2026-04-20T10:05:00+00:00", "duration_seconds": 300.0,
        "error_message": None,
        "stats": {
            "total_assets": 1000,
            "total_assets_created": 1403,   # combined (untouched)
            "total_assets_updated": 200,
            "removed_assets": 5,
            "batches_processed": 3,
            "chronicle_events": {"asset_created": 319, "asset_readded": 1084},
        },
    }

    out = list_pipeline_runs_impl(svc, run_id=run_id)

    assert "- created: 319" in out        # new only, not the combined 1403
    assert "- returned: 1084" in out
    assert "- created: 1403" not in out


def test_detail_chronicle_present_without_create_keys_shows_zero():
    """chronicle_events present but lacking asset_created/asset_readded (e.g. an
    update/remove-only run) yields created:0 + returned:0 — NOT the combined
    total_assets_created. Guards the truthy-but-missing-key edge of the split."""
    svc = MagicMock()
    run_id = "11111111-1111-1111-1111-111111111111"
    svc.get_run.return_value = {
        "id": run_id, "triggered_by": "schedule", "status": "completed",
        "current_stage": None, "started_at": None, "completed_at": None,
        "duration_seconds": None, "error_message": None,
        "stats": {
            "total_assets_created": 42,  # combined — must NOT leak as created
            "chronicle_events": {"attribute_updated": 7, "asset_removed": 3},
        },
    }

    out = list_pipeline_runs_impl(svc, run_id=run_id)

    assert "- created: 0" in out
    assert "- returned: 0" in out
    assert "- created: 42" not in out


def test_detail_batch_results_capped_with_overflow_signal():
    # A heavily-integrated run can carry dozens of batches; rendering all of them
    # into context is the §8-#2 blow-up. The header keeps the real count and the
    # tail is announced, so nothing is silently dropped.
    from src.mcp.tools._common import CHAT_TEXT_ROW_CAP
    svc = MagicMock()
    run_id = "11111111-1111-1111-1111-111111111111"
    batches = [
        {"batch_id": f"b{i}", "success": True, "input_records": 10,
         "integration_name": f"src{i}", "unique_assets": 5, "error": None}
        for i in range(CHAT_TEXT_ROW_CAP + 6)
    ]
    svc.get_run.return_value = {
        "id": run_id, "triggered_by": "manual", "status": "completed",
        "current_stage": None, "started_at": "2026-04-20T10:00:00+00:00",
        "completed_at": "2026-04-20T10:05:00+00:00", "duration_seconds": 300.0,
        "error_message": None,
        "stats": {"total_assets": 10, "batch_results": batches},
    }
    out = list_pipeline_runs_impl(svc, run_id=run_id)
    assert f"## Batch results ({CHAT_TEXT_ROW_CAP + 6})" in out  # real total
    assert "src0" in out and f"src{CHAT_TEXT_ROW_CAP - 1}" in out
    assert f"| src{CHAT_TEXT_ROW_CAP} |" not in out             # tail capped
    assert "6 more batches not shown" in out


# --- phases breakdown -------------------------------------------------------

def test_phases_requires_run_id():
    svc = MagicMock()
    out = list_pipeline_runs_impl(svc, phases=True)
    assert "run_id" in out
    svc.get_runs.assert_not_called()


def test_phases_invalid_uuid_does_not_call_phase_service():
    svc = MagicMock()
    phase = MagicMock()
    out = list_pipeline_runs_impl(svc, run_id="nope", phases=True,
                                  phase_service=phase)
    assert "Invalid pipeline run ID" in out
    phase.get_phases_for_run.assert_not_called()


def test_phases_renders_substage_timings():
    svc = MagicMock()
    phase = MagicMock()
    run_id = "11111111-1111-1111-1111-111111111111"
    phase.get_phases_for_run.return_value = {
        "run_id": run_id,
        "status": "completed",
        "phases": [{
            "key": "ingest", "label": "Ingestion", "status": "success",
            "duration_seconds": 30.0, "has_slow_stages": True,
            "sub_stages": [{"stage_key": "s01_load", "duration_seconds": 25.0,
                            "p95_seconds": 10.0, "samples": 8,
                            "is_slow": True}],
        }],
    }
    out = list_pipeline_runs_impl(svc, run_id=run_id, phases=True,
                                  phase_service=phase)
    assert "phase breakdown" in out
    assert "Ingestion" in out
    assert "s01_load" in out
    assert "slow" in out.lower()


def test_phases_unavailable_without_phase_service():
    svc = MagicMock()
    run_id = "11111111-1111-1111-1111-111111111111"
    out = list_pipeline_runs_impl(svc, run_id=run_id, phases=True,
                                  phase_service=None)
    assert "not available" in out.lower()


# --- review fixes -----------------------------------------------------------

def test_page_as_string_is_coerced_not_typeerror():
    # The chat registry does not coerce arg types; an LLM that emits page as a
    # JSON string "2" must still page, not crash with an opaque TypeError from
    # max("2", 1).
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [_run()], "total": 1, "page": 1, "per_page": 20, "pages": 1,
    }
    out = list_pipeline_runs_impl(svc, page="1", per_page="20")
    assert "# Pipeline runs" in out


def test_end_date_date_only_covers_whole_day_and_is_utc():
    # An inclusive end_date given as a bare date must cover the WHOLE day, not
    # be truncated to midnight (which would silently drop that day's runs).
    dt, err = _parse_date("2026-04-20", "end_date", end_of_day=True)
    assert err is None
    assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)
    assert dt.tzinfo is not None  # UTC-pinned, not a naive value


def test_start_date_date_only_is_midnight_utc():
    dt, err = _parse_date("2026-04-20", "start_date")
    assert err is None
    assert (dt.hour, dt.minute, dt.second) == (0, 0, 0)
    assert dt.tzinfo is not None


def test_explicit_time_is_not_widened_to_end_of_day():
    dt, _ = _parse_date("2026-04-20T10:30:00", "end_date", end_of_day=True)
    assert (dt.hour, dt.minute) == (10, 30)


def test_page_past_end_unfiltered_does_not_imply_empty_system():
    # Empty + unfiltered + page>1 means we paged off the end, not that no runs
    # exist — the message must say so, not "No pipeline runs recorded."
    svc = MagicMock()
    svc.get_runs.return_value = {
        "runs": [], "total": 5, "page": 3, "per_page": 20, "pages": 1,
    }
    out = list_pipeline_runs_impl(svc, page=3)
    assert "past the last page" in out
    assert "No pipeline runs recorded" not in out


def test_current_status_failure_does_not_sink_run_list():
    # A failure building the live-status header must not lose the run list.
    svc = MagicMock()
    svc.get_current_status.side_effect = RuntimeError("db down")
    svc.get_runs.return_value = {
        "runs": [_run()], "total": 1, "page": 1, "per_page": 20, "pages": 1,
    }
    out = list_pipeline_runs_impl(svc)
    assert "# Pipeline runs" in out
    assert "11111111-1111-1111-1111-111111111111" in out
    assert "# Pipeline status" not in out  # header gracefully omitted
    assert "db down" not in out            # no raw exception leak
