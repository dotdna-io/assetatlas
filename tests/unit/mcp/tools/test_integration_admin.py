# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the integration ingestion-debug & catalog chat tools.

These impls wrap the integration service/repository layer (never raw SQL) and
back five admin-gated chat tools. Each test pins a behaviour the parity push
requires (see tasks/chatbot-tool-parity.md §2.E, §4, §6.1):

- An integration reference resolves by id OR case-insensitive name.
- Bad input (unknown enum, unknown integration) returns a LOUD error string,
  never a silently-empty result that would read as "nothing there".
- List results carry a real total COUNT + paging, with an overflow signal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock

from src.modules.integrations.models.integration import (
    Integration,
    IntegrationStatus,
)
from src.mcp.tools import integration_admin
from src.mcp.tools.integration_admin import (
    get_batch_impl,
    get_integration_mapping_impl,
    get_integration_schedule_impl,
    list_integration_batches_impl,
    list_integrations_impl,
    _resolve_integration,
)


_IID = "11111111-1111-1111-1111-111111111111"
_BID = "22222222-2222-2222-2222-222222222222"
_IID2 = "33333333-3333-3333-3333-333333333333"
_GID = "44444444-4444-4444-4444-444444444444"


def _integ(name="Beszel", iid=_IID, **kw):
    return Integration(
        name=name,
        integration_type=kw.get("integration_type", "custom_http"),
        category=kw.get("category", "Monitoring"),
        config=kw.get("config", {"api_key": "secret"}),
        field_mapping=kw.get("field_mapping", {}),
        id=uuid.UUID(iid) if isinstance(iid, str) else iid,
        status=kw.get("status", IntegrationStatus.ENABLED),
        last_sync_at=kw.get("last_sync_at"),
        last_sync_status=kw.get("last_sync_status"),
        schedule_enabled=kw.get("schedule_enabled", False),
        schedule_cron=kw.get("schedule_cron"),
        schedule_timezone=kw.get("schedule_timezone", "UTC"),
        next_run_at=kw.get("next_run_at"),
        is_running=kw.get("is_running", False),
        group_name=kw.get("group_name"),
        group_id=kw.get("group_id"),
    )


def _batch(status="completed", started="2026-06-01 10:00:00",
           completed="2026-06-01 10:05:00", raw=100, mapped=95, created=40,
           error=None, bid=_BID):
    return {
        "id": uuid.UUID(bid),
        "integration_id": uuid.UUID(_IID),
        "status": status,
        "started_at": datetime.fromisoformat(started) if started else None,
        "completed_at": datetime.fromisoformat(completed) if completed else None,
        "raw_records_count": raw,
        "mapped_records_count": mapped,
        "assets_created_count": created,
        "error_message": error,
    }


# --------------------------------------------------------------------------
# _resolve_integration — id OR case-insensitive name
# --------------------------------------------------------------------------

def test_resolve_by_id_uses_get_integration():
    svc = MagicMock()
    integ = _integ()
    svc.get_integration.return_value = integ

    result, err = _resolve_integration(svc, _IID)

    assert err is None
    assert result is integ
    # Resolved by id — no need to scan the full list.
    svc.list_integrations.assert_not_called()


def test_resolve_by_name_is_case_insensitive():
    svc = MagicMock()
    integ = _integ(name="Beszel")
    svc.list_integrations.return_value = [integ]

    result, err = _resolve_integration(svc, "beszel")

    assert err is None
    assert result is integ


def test_resolve_unknown_returns_loud_error_listing_known_names():
    """A missing integration must NOT resolve to None-and-empty downstream;
    it must hand the model a corrective message naming what *does* exist."""
    svc = MagicMock()
    svc.list_integrations.return_value = [_integ(name="Beszel"),
                                          _integ(name="Jamf Pro")]

    result, err = _resolve_integration(svc, "Crowdstrike")

    assert result is None
    assert err  # non-empty string
    assert "Beszel" in err and "Jamf Pro" in err


# --------------------------------------------------------------------------
# list_integration_batches
# --------------------------------------------------------------------------

def test_batches_happy_path_renders_fields_and_total():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.batch_repository.list_batches.return_value = [
        _batch(status="completed", raw=100, mapped=95, created=40),
    ]
    svc.batch_repository.count_batches.return_value = 1

    out = list_integration_batches_impl(svc, integration=_IID)

    assert "completed" in out
    assert "100" in out          # total_records (raw_records_count)
    assert "95" in out           # records_mapped
    assert "40" in out           # assets_created
    assert "300" in out          # duration seconds (10:00 -> 10:05)


def test_batches_paging_shows_true_total_and_overflow_signal():
    """With more batches than the page holds, the tool must report the real
    COUNT and signal that more exist — never imply the page is the whole set."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.batch_repository.list_batches.return_value = [_batch(), _batch()]
    svc.batch_repository.count_batches.return_value = 7

    out = list_integration_batches_impl(svc, integration=_IID, limit=2, offset=0)

    assert "7" in out                 # true total surfaced
    assert "more" in out.lower()      # overflow signal


def test_batches_unknown_status_is_loud_error_not_empty_list():
    """A bogus status must error loudly. Passing it through would return an
    empty batch list that reads as 'this integration never ran' — wrong."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ()

    out = list_integration_batches_impl(svc, integration=_IID, status="bogus")

    assert "Valid values" in out
    svc.batch_repository.list_batches.assert_not_called()


def test_batches_valid_status_filters_through_to_repo():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.batch_repository.list_batches.return_value = [_batch(status="failed")]
    svc.batch_repository.count_batches.return_value = 1

    list_integration_batches_impl(svc, integration=_IID, status="failed")

    list_args = svc.batch_repository.list_batches.call_args
    assert "failed" in list_args[0] or list_args[1].get("status") == "failed"
    # The COUNT must carry the SAME status filter, or the rendered total would
    # describe the whole table while the page shows only failed batches.
    count_args = svc.batch_repository.count_batches.call_args
    assert "failed" in count_args[0] or count_args[1].get("status") == "failed"


def test_batches_mapping_status_is_accepted():
    """'mapping' is a real ingestion_batches status (DB CHECK constraint +
    mapping_service writes it). It must be a filterable value, not rejected
    as 'unknown' — the omission would block a legitimate debug query."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.batch_repository.list_batches.return_value = [_batch(status="mapping")]
    svc.batch_repository.count_batches.return_value = 1

    out = list_integration_batches_impl(svc, integration=_IID, status="mapping")

    assert "Valid values" not in out  # not rejected as an unknown enum
    assert "mapping" in out
    svc.batch_repository.list_batches.assert_called_once()


def test_batches_unknown_integration_is_loud_error():
    svc = MagicMock()
    svc.get_integration.return_value = None
    svc.list_integrations.return_value = [_integ(name="Beszel")]

    out = list_integration_batches_impl(svc, integration="Nope")

    assert "Beszel" in out  # names the known integrations
    svc.batch_repository.list_batches.assert_not_called()


def test_batches_empty_is_friendly_not_an_error():
    """Zero batches for a real integration is a legitimate empty, distinct
    from a resolution/validation failure — say so plainly with total 0."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ(name="Beszel")
    svc.batch_repository.list_batches.return_value = []
    svc.batch_repository.count_batches.return_value = 0

    out = list_integration_batches_impl(svc, integration=_IID)

    assert "Beszel" in out
    assert "0" in out


# --------------------------------------------------------------------------
# get_batch (detail | raw | mapped)
# --------------------------------------------------------------------------

def _raw_record(rid=1, source_id="src-1", host="host-a"):
    return {
        "id": rid,
        "source_record_id": source_id,
        "data": {"hostname": host, "ip": "10.0.0.1"},
        "collected_at": datetime.fromisoformat("2026-06-01 10:00:00"),
    }


def _mapped_record(rid=1, host="host-a"):
    return {
        "id": rid,
        "integration_id": uuid.UUID(_IID),
        "raw_source_id": 1,
        "hostname": [host],
        "ip": ["10.0.0.1"],
        "os": "Ubuntu 24.04",
        "extended_fields": {"cpu": "8"},
        "mapped_at": datetime.fromisoformat("2026-06-01 10:01:00"),
    }


def test_get_batch_invalid_uuid_is_loud_error():
    svc = MagicMock()

    out = get_batch_impl(svc, batch_id="not-a-uuid")

    assert "Invalid batch ID" in out
    svc.batch_repository.get_batch.assert_not_called()


def test_get_batch_unknown_view_is_loud_error():
    svc = MagicMock()

    out = get_batch_impl(svc, batch_id=_BID, view="bogus")

    assert "Valid values" in out
    svc.batch_repository.get_batch.assert_not_called()


def test_get_batch_not_found():
    svc = MagicMock()
    svc.batch_repository.get_batch.return_value = None

    out = get_batch_impl(svc, batch_id=_BID)

    assert "not found" in out.lower()
    assert _BID[:8] in out


def test_get_batch_detail_renders_summary_without_fetching_records():
    svc = MagicMock()
    svc.batch_repository.get_batch.return_value = _batch(
        status="completed", raw=100, mapped=95, created=40,
    )

    out = get_batch_impl(svc, batch_id=_BID, view="detail")

    assert "completed" in out
    assert "100" in out and "95" in out and "40" in out
    assert "300" in out  # duration seconds
    # detail view must not pull record pages
    svc.raw_data_repository.get_records.assert_not_called()


def test_get_batch_raw_renders_records_with_total_and_overflow():
    svc = MagicMock()
    svc.batch_repository.get_batch.return_value = _batch()
    svc.raw_data_repository.get_records.return_value = [_raw_record(host="raw-host")]
    svc.raw_data_repository.count_records.return_value = 9

    out = get_batch_impl(svc, batch_id=_BID, view="raw", limit=1, offset=0)

    assert "raw-host" in out      # the actual raw payload value
    # "of 9" pins the real record total to the page header — a bare "9" would
    # also match the summary's "mapped 95", hiding a broken total.
    assert "of 9" in out
    assert "more" in out.lower()  # overflow signal


def test_get_batch_mapped_uses_raw_asset_repo(monkeypatch):
    svc = MagicMock()
    svc.batch_repository.get_batch.return_value = _batch()

    fake_repo = MagicMock()
    fake_repo.get_by_batch.return_value = [_mapped_record(host="mapped-host")]
    fake_repo.count_by_batch.return_value = 5
    monkeypatch.setattr(
        integration_admin, "RawAssetRepository", lambda conn: fake_repo,
    )

    out = get_batch_impl(svc, batch_id=_BID, view="mapped", limit=1, offset=0)

    assert "mapped-host" in out
    assert "of 5" in out          # true total of mapped records (not page len)
    assert "more" in out.lower()
    # never touched the raw-source path for a mapped request
    svc.raw_data_repository.get_records.assert_not_called()


# --------------------------------------------------------------------------
# get_integration_mapping (mapping_preview | raw_sample | schema)
# --------------------------------------------------------------------------

def _preview_payload(dropped=3, total=10, filtered=10):
    return {
        "success": True,
        "batch_id": _BID,
        "batch_completed_at": "2026-06-01 10:05:00",
        "total_records": total,
        "filtered_total": filtered,
        "dropped_count": dropped,
        "columns": ["hostname", "ip", "os"],
        "source_map": {"computer.name": "hostname"},
        "transform_map": {},
        "field_stats": [],
        "records": [
            {
                "row_num": 1,
                "raw": {"computer": {"name": "host-a"}},
                "mapped": {"hostname": ["host-a"]},
                "dropped": True,
                "drop_reasons": ["no valid hostname"],
            },
        ],
    }


def test_mapping_unknown_view_is_loud_error():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    preview = MagicMock()

    out = get_integration_mapping_impl(
        svc, preview, integration=_IID, view="bogus",
    )

    assert "Valid values" in out
    preview.get_mapping_preview.assert_not_called()


def test_mapping_preview_renders_drops_columns_and_total():
    svc = MagicMock()
    svc.get_integration.return_value = _integ(name="Beszel")
    preview = MagicMock()
    preview.get_mapping_preview.return_value = _preview_payload(
        dropped=3, total=10, filtered=10,
    )

    out = get_integration_mapping_impl(
        svc, preview, integration=_IID, view="mapping_preview", status="all",
    )

    assert "3" in out                       # dropped_count
    assert "10" in out                      # total records
    assert "hostname" in out                # a mapped column
    assert "no valid hostname" in out       # a drop reason
    # delegated to the service with the resolved id + status
    args = preview.get_mapping_preview.call_args
    assert args[0][0] == _IID               # str(integration.id)
    assert args[1].get("status") == "all"


def test_mapping_preview_unknown_status_is_loud_error():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    preview = MagicMock()

    out = get_integration_mapping_impl(
        svc, preview, integration=_IID, view="mapping_preview", status="weird",
    )

    assert "Valid values" in out
    preview.get_mapping_preview.assert_not_called()


def test_mapping_preview_service_unavailable_is_graceful():
    """If the preview service isn't wired, say so — don't crash on None."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ()

    out = get_integration_mapping_impl(
        svc, None, integration=_IID, view="mapping_preview",
    )

    assert "not available" in out.lower() or "unavailable" in out.lower()


def test_mapping_preview_no_field_mapping_is_surfaced():
    """A source with no field mapping is a real, reportable state — not an
    empty page that reads as 'mapping is fine'."""
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    preview = MagicMock()
    preview.get_mapping_preview.return_value = {
        "success": True,
        "total_records": 0,
        "filtered_total": 0,
        "dropped_count": 0,
        "columns": [],
        "records": [],
        "error": "No field mapping configured",
    }

    out = get_integration_mapping_impl(
        svc, preview, integration=_IID, view="mapping_preview",
    )

    assert "No field mapping configured" in out


def test_mapping_raw_sample_renders_records_and_inferred_schema():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.batch_repository.get_latest_completed_batch.return_value = {
        "id": uuid.UUID(_BID),
        "completed_at": datetime.fromisoformat("2026-06-01 10:05:00"),
    }
    svc.raw_data_repository.get_records.return_value = [_raw_record(host="sample-host")]
    svc.raw_data_repository.count_records.return_value = 4

    out = get_integration_mapping_impl(
        svc, MagicMock(), integration=_IID, view="raw_sample", limit=1,
    )

    assert "sample-host" in out          # raw payload value
    assert "hostname" in out             # inferred schema path
    assert "4" in out                    # true total of raw records


def test_mapping_raw_sample_no_completed_batch_is_friendly():
    svc = MagicMock()
    svc.get_integration.return_value = _integ(name="Beszel")
    svc.batch_repository.get_latest_completed_batch.return_value = None

    out = get_integration_mapping_impl(
        svc, MagicMock(), integration=_IID, view="raw_sample",
    )

    assert "no completed batch" in out.lower()
    svc.raw_data_repository.get_records.assert_not_called()


def test_mapping_schema_view_returns_inferred_schema():
    svc = MagicMock()
    svc.get_integration.return_value = _integ()
    svc.batch_repository.get_latest_completed_batch.return_value = {
        "id": uuid.UUID(_BID),
        "completed_at": datetime.fromisoformat("2026-06-01 10:05:00"),
    }
    svc.raw_data_repository.get_records.return_value = [
        _raw_record(host="x"), _raw_record(rid=2, source_id="src-2", host="y"),
    ]

    out = get_integration_mapping_impl(
        svc, MagicMock(), integration=_IID, view="schema",
    )

    assert "hostname" in out      # inferred field path
    assert "ip" in out            # second inferred path


# --------------------------------------------------------------------------
# list_integrations (integrations | groups | connectors | stats)
# --------------------------------------------------------------------------

def test_list_integrations_unknown_view_is_loud_error():
    svc = MagicMock()

    out = list_integrations_impl(svc, MagicMock(), view="bogus")

    assert "Valid values" in out
    svc.list_integrations.assert_not_called()


def test_list_integrations_view_renders_count_and_never_leaks_config():
    """The catalog lists configured integrations with a real count, and must
    NEVER render the integration config (which holds API keys/passwords)."""
    svc = MagicMock()
    svc.list_integrations.return_value = [
        _integ(name="Beszel", iid=_IID, config={"api_key": "supersecret"}),
        _integ(name="Jamf Pro", iid=_IID2, integration_type="jamf"),
    ]
    svc.batch_repository.get_latest_batches_for_all_integrations.return_value = {
        _IID: {
            "raw_records_count": 100,
            "assets_created_count": 40,
            "status": "completed",
            "started_at": datetime.fromisoformat("2026-06-01 10:00:00"),
            "last_sync_at": datetime.fromisoformat("2026-06-01 10:05:00"),
            "last_run_at": datetime.fromisoformat("2026-06-01 10:05:00"),
        },
    }

    out = list_integrations_impl(svc, MagicMock(), view="integrations")

    assert "Beszel" in out and "Jamf Pro" in out
    assert "2" in out               # real total count
    assert "100" in out             # latest-batch enrichment
    assert "supersecret" not in out  # credentials never leak


def test_list_integrations_view_caps_rows_with_overflow_signal():
    # A fleet of 100+ integrations must not dump every row into context: the
    # listing is capped and the omission announced (§3.5/§8-#2). Without this a
    # heavily-integrated deployment blows the context window on a single call.
    from src.mcp.tools._common import CHAT_TEXT_ROW_CAP
    svc = MagicMock()
    svc.list_integrations.return_value = [
        _integ(name=f"integ-{i:03d}",
               iid=f"{i:08d}-1111-1111-1111-111111111111")
        for i in range(CHAT_TEXT_ROW_CAP + 7)
    ]
    svc.batch_repository.get_latest_batches_for_all_integrations.return_value = {}

    out = list_integrations_impl(svc, MagicMock(), view="integrations")

    assert "integ-000" in out                       # first rows render
    assert f"integ-{CHAT_TEXT_ROW_CAP:03d}" not in out  # tail is capped
    assert "7 more integrations not shown" in out   # overflow announced
    assert str(CHAT_TEXT_ROW_CAP + 7) in out        # header still shows real total


def test_list_groups_view_includes_members():
    svc = MagicMock()
    grp_svc = MagicMock()
    grp_svc.list_groups.return_value = [
        {"id": uuid.UUID(_GID), "name": "S1 Group",
         "integration_type": "sentinelone", "member_count": 2},
    ]
    svc.list_integrations.return_value = [
        _integ(name="S1-East", iid=_IID, group_id=uuid.UUID(_GID)),
        _integ(name="S1-West", iid=_IID2, group_id=uuid.UUID(_GID)),
    ]

    out = list_integrations_impl(svc, grp_svc, view="groups")

    assert "S1 Group" in out
    assert "S1-East" in out and "S1-West" in out
    assert "2" in out  # member_count


def test_list_groups_service_unavailable_is_graceful():
    svc = MagicMock()

    out = list_integrations_impl(svc, None, view="groups")

    assert "not available" in out.lower() or "unavailable" in out.lower()


def test_list_connectors_view_renders_types_and_config_fields():
    svc = MagicMock()
    # Real ConnectorRegistry shape: direct_connector is the type STRING for
    # technology connectors (None for transport-only), never a bool.
    svc.get_available_connectors.return_value = [
        {
            "integration_type": "jamf",
            "display_name": "Jamf Pro",
            "category": "MDM",
            "connector_role": "technology",
            "direct_connector": "jamf",
            "config_schema": {
                "properties": {
                    "base_url": {"type": "string"},
                    "api_token": {"type": "string"},
                },
                "required": ["base_url"],
            },
        },
    ]

    out = list_integrations_impl(svc, MagicMock(), view="connectors")

    assert "jamf" in out
    assert "Jamf Pro" in out
    assert "role=technology" in out  # canonical direct-vs-transport indicator
    assert "base_url" in out  # a config-schema field


def test_list_stats_view_includes_integration_and_batch_aggregates():
    svc = MagicMock()
    svc.get_integration_stats.return_value = {
        "total": 5, "enabled": 4, "disabled": 1, "error": 0,
        "available_connectors": 30,
    }
    svc.batch_repository.get_global_batch_stats.return_value = {
        "total": 120, "completed": 100, "failed": 5, "processing": 0,
        "partial": 15, "total_records": 50000, "total_mapped": 48000,
        "total_assets_created": 9000, "total_assets_updated": 1000,
        "last_successful_batch": None, "success_rate": 83.3,
    }

    out = list_integrations_impl(svc, MagicMock(), view="stats")

    assert "5" in out       # integration total
    assert "120" in out     # batch total
    assert "50000" in out   # total raw records
    assert "9000" in out    # total assets created


# --------------------------------------------------------------------------
# get_integration_schedule
# --------------------------------------------------------------------------

def test_schedule_unknown_integration_is_loud_error():
    svc = MagicMock()
    svc.get_integration.return_value = None
    svc.list_integrations.return_value = [_integ(name="Beszel")]

    out = get_integration_schedule_impl(svc, integration="Nope")

    assert "Beszel" in out


def test_schedule_renders_cron_tz_and_next_runs(monkeypatch):
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        name="Beszel",
        schedule_enabled=True,
        schedule_cron="0 * * * *",
        schedule_timezone="UTC",
        next_run_at=datetime.fromisoformat("2026-06-19 15:00:00"),
        is_running=False,
    )
    runs = [
        datetime.fromisoformat("2026-06-19 16:00:00"),
        datetime.fromisoformat("2026-06-19 17:00:00"),
    ]
    monkeypatch.setattr(
        integration_admin.cron_validator, "calculate_next_runs",
        lambda cron, tz, count: runs,
    )
    monkeypatch.setattr(
        integration_admin.cron_validator, "get_cron_description",
        lambda cron: "Every hour",
    )

    out = get_integration_schedule_impl(svc, integration=_IID, count=2)

    assert "0 * * * *" in out      # cron
    assert "UTC" in out            # timezone
    assert "Every hour" in out     # description
    assert "2026-06-19 16:00:00" in out  # a computed next run
    assert "2026-06-19 17:00:00" in out


def test_schedule_no_cron_reported_without_computing_runs(monkeypatch):
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        schedule_enabled=False, schedule_cron=None, is_running=True,
    )
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(
        integration_admin.cron_validator, "calculate_next_runs", _spy,
    )

    out = get_integration_schedule_impl(svc, integration=_IID)

    assert "no" in out.lower() and "schedule" in out.lower()
    assert "running" in out.lower()  # still surfaces is_running
    assert called["n"] == 0          # no cron -> no run computation


def test_schedule_count_is_clamped_to_20(monkeypatch):
    svc = MagicMock()
    svc.get_integration.return_value = _integ(
        schedule_cron="0 * * * *", schedule_timezone="UTC",
    )
    seen = {}

    def _capture(cron, tz, count):
        seen["count"] = count
        return []

    monkeypatch.setattr(
        integration_admin.cron_validator, "calculate_next_runs", _capture,
    )
    monkeypatch.setattr(
        integration_admin.cron_validator, "get_cron_description",
        lambda cron: "desc",
    )

    get_integration_schedule_impl(svc, integration=_IID, count=100)

    assert seen["count"] == 20  # never asks the scheduler for more than 20
