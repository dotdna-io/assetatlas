# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the list_enrichment_datasets MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from src.mcp.tools.enrichment import list_enrichment_datasets_impl


def test_list_form_renders_all_datasets():
    db = MagicMock()
    db.execute_query.return_value = [
        {"id": str(uuid4()), "name": "Asset Owners", "match_field": "hostname",
         "target_field": "owner", "row_count": 100, "matched_count": 80,
         "status": "active", "updated_at": "2026-04-20T10:00:00Z"},
        {"id": str(uuid4()), "name": "Site Map", "match_field": "ip",
         "target_field": "site", "row_count": 50, "matched_count": 50,
         "status": "active", "updated_at": "2026-04-19T10:00:00Z"},
        {"id": str(uuid4()), "name": "Cost Centers", "match_field": "department",
         "target_field": "cost_center", "row_count": 25, "matched_count": 10,
         "status": "draft", "updated_at": "2026-04-18T10:00:00Z"},
    ]

    out = list_enrichment_datasets_impl(db)

    assert "Asset Owners" in out
    assert "Site Map" in out
    assert "Cost Centers" in out
    assert "hostname" in out and "owner" in out
    assert "→" in out
    assert "80/100" in out
    assert "50/50" in out
    assert "10/25" in out


def test_list_form_empty():
    db = MagicMock()
    db.execute_query.return_value = []
    out = list_enrichment_datasets_impl(db)
    assert "No enrichment datasets defined." in out


def test_detail_form_with_sample_rows():
    db = MagicMock()
    did = str(uuid4())
    db.execute_query.side_effect = [
        [{"id": did, "name": "Asset Owners", "match_field": "hostname",
          "match_type": "exact", "target_field": "owner", "status": "active",
          "row_count": 100, "matched_count": 80, "uploaded_by": "alice",
          "created_at": "2026-04-01T10:00:00Z",
          "updated_at": "2026-04-20T10:00:00Z"}],
        [{"match_value": "srv-1", "enrich_value": "Alice"},
         {"match_value": "wks-2", "enrich_value": "Bob"}],
    ]

    out = list_enrichment_datasets_impl(db, dataset_id=did)
    assert "Asset Owners" in out
    assert "srv-1 → Alice" in out
    assert "wks-2 → Bob" in out
    assert "alice" in out
    assert "exact" in out


def test_detail_form_not_found():
    db = MagicMock()
    db.execute_query.return_value = []
    out = list_enrichment_datasets_impl(db, dataset_id=str(uuid4()))
    assert "not found" in out.lower()


def test_detail_form_invalid_uuid():
    db = MagicMock()
    out = list_enrichment_datasets_impl(db, dataset_id="not-a-uuid")
    assert "Invalid dataset ID" in out
    db.execute_query.assert_not_called()


# --- list: paging, real total, search, deleted-exclusion --------------------

def test_list_real_total_and_overflow_signal():
    db = MagicMock()
    # COUNT(*) OVER() reports the true total (25) though only one row fits the
    # page → an explicit overflow line, not silent truncation (§4).
    db.execute_query.return_value = [
        {"id": str(uuid4()), "name": "DS1", "match_field": "hostname",
         "match_type": "exact", "target_field": "owner", "status": "active",
         "row_count": 10, "matched_count": 5, "uploaded_by": "a",
         "created_at": "2026-04-20T10:00:00Z", "updated_at": None,
         "total_count": 25},
    ]
    out = list_enrichment_datasets_impl(db, page=1, per_page=1)
    assert "total 25" in out
    assert "more not shown" in out


def test_list_excludes_deleted_and_applies_search():
    db = MagicMock()
    db.execute_query.return_value = []
    list_enrichment_datasets_impl(db, search="owners")
    sql = db.execute_query.call_args.args[0]
    params = db.execute_query.call_args.args[1]
    # Soft-deleted rows are hidden (matches the UI) and search is an ILIKE.
    assert "status != %(excluded)s" in sql
    assert params["excluded"] == "deleted"
    assert "name ILIKE %(search)s" in sql
    assert params["search"] == "%owners%"


def test_list_empty_with_filter_explains_filter():
    db = MagicMock()
    db.execute_query.return_value = []
    out = list_enrichment_datasets_impl(db, search="nomatch")
    assert "filter" in out.lower()


def test_list_page_past_end_does_not_imply_empty_system():
    # Empty + unfiltered + page>1 means we paged off the end, not that there
    # are no datasets — don't say "No enrichment datasets defined."
    db = MagicMock()
    db.execute_query.return_value = []
    out = list_enrichment_datasets_impl(db, page=3)
    assert "past the last page" in out
    assert "No enrichment datasets defined" not in out


# --- detail: deterministic + honored sample_limit + created_at --------------

def test_detail_sample_limit_honored_and_deterministic():
    db = MagicMock()
    did = str(uuid4())
    db.execute_query.side_effect = [
        [{"id": did, "name": "DS", "match_field": "hostname",
          "match_type": "exact", "target_field": "owner", "status": "active",
          "row_count": 100, "matched_count": 80, "uploaded_by": "alice",
          "created_at": "2026-04-01T10:00:00Z", "updated_at": "x"}],
        [{"match_value": "a", "enrich_value": "1"}],
    ]
    out = list_enrichment_datasets_impl(db, dataset_id=did, sample_limit=3)
    # created_at is now surfaced (was fetched but never shown).
    assert "created:" in out
    samples_sql = db.execute_query.call_args_list[1].args[0]
    samples_params = db.execute_query.call_args_list[1].args[1]
    assert "ORDER BY match_value" in samples_sql  # deterministic
    assert samples_params["lim"] == 3             # honored, not fixed at 5


# --- preview mode -----------------------------------------------------------

def test_preview_requires_dataset_id():
    db = MagicMock()
    out = list_enrichment_datasets_impl(db, preview=True)
    assert "requires a dataset_id" in out


def test_preview_delegates_to_service_and_reports_count():
    db = MagicMock()
    svc = MagicMock()
    did = str(uuid4())
    svc.preview_dataset.return_value = {
        "dataset_name": "Owners", "matched_count": 42, "total_rows": 100,
        "sample_matches": [
            {"hostname": "srv-1", "matched_against": "srv-1",
             "enrich_value": "Alice"}],
    }
    out = list_enrichment_datasets_impl(db, enrichment_service=svc,
                                        dataset_id=did, preview=True)
    svc.preview_dataset.assert_called_once_with(did)
    assert "42" in out
    assert "srv-1" in out


def test_preview_invalid_uuid_is_loud_error():
    db = MagicMock()
    svc = MagicMock()
    out = list_enrichment_datasets_impl(db, enrichment_service=svc,
                                        dataset_id="bad", preview=True)
    assert "Invalid dataset ID" in out
    svc.preview_dataset.assert_not_called()


def test_preview_without_service_is_graceful():
    db = MagicMock()
    out = list_enrichment_datasets_impl(db, enrichment_service=None,
                                        dataset_id=str(uuid4()), preview=True)
    assert "not available" in out.lower()


# --- ext_fields view --------------------------------------------------------

def test_ext_fields_delegates_to_service():
    db = MagicMock()
    svc = MagicMock()
    svc.list_ext_fields.return_value = [
        {"source": "Beszel", "key": "cpu", "path": "ext.Beszel.cpu",
         "sample_value": "12%"},
    ]
    out = list_enrichment_datasets_impl(db, enrichment_service=svc,
                                        ext_fields=True)
    svc.list_ext_fields.assert_called_once()
    assert "ext.Beszel.cpu" in out
    assert "Beszel" in out


def test_ext_fields_without_service_is_graceful():
    db = MagicMock()
    out = list_enrichment_datasets_impl(db, enrichment_service=None,
                                        ext_fields=True)
    assert "not available" in out.lower()
