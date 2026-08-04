# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end integration test: walks the pipeline-watchdog state machine
through stale / last-run-failed / stuck scenarios against a live DB."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.modules.alerts.repository import AlertsRepository
from src.modules.pipeline_watchdog.repository import PipelineWatchdogRepository
from src.modules.pipeline_watchdog.service import PipelineWatchdogService


def _reset_state(db):
    """Wipe runs and any pre-existing active alerts for the three watchdog rules."""
    db.execute_query("DELETE FROM pipeline_runs", fetch=False)
    db.execute_query(
        """
        DELETE FROM alerts
        WHERE rule_id IN (
            SELECT id FROM alert_rules
            WHERE slug IN ('pipeline-stale', 'pipeline-last-run-failed', 'pipeline-stuck')
        )
        """,
        fetch=False,
    )


def _insert_run(db, status, started_at=None, completed_at=None,
                created_at=None, error_message=None):
    run_id = str(uuid.uuid4())
    db.execute_query(
        """
        INSERT INTO pipeline_runs
            (id, triggered_by, status, started_at, completed_at,
             error_message, created_at)
        VALUES
            (%(id)s::uuid, 'test', %(status)s, %(started_at)s, %(completed_at)s,
             %(error_message)s, COALESCE(%(created_at)s, now()))
        """,
        {
            "id": run_id, "status": status,
            "started_at": started_at, "completed_at": completed_at,
            "error_message": error_message, "created_at": created_at,
        },
        fetch=False,
    )
    return run_id


def _set_setting(db, category, key, value, value_type):
    """Upsert a system_settings row. value column is TEXT (see migration 042)
    and holds a JSON-encoded string literal; _get_setting json-parses it.

    value_type is one of 'boolean', 'integer', 'string', 'json' — mirrors the
    NOT NULL column the existing seed migrations populate.
    """
    db.execute_query(
        """
        INSERT INTO system_settings
            (category, key, value, value_type, default_value)
        VALUES
            (%(category)s, %(key)s, %(value)s, %(value_type)s, %(default_value)s)
        ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value
        """,
        {
            "category": category, "key": key, "value": value,
            "value_type": value_type, "default_value": value,
        },
        fetch=False,
    )


def _count_active_alerts(db, slug):
    rows = db.execute_query(
        """
        SELECT COUNT(*)::int AS n
        FROM alerts a
        JOIN alert_rules r ON r.id = a.rule_id
        WHERE r.slug = %(slug)s AND a.status = 'active'
        """,
        {"slug": slug},
    )
    return rows[0]["n"]


def _resolved_at(db, slug):
    rows = db.execute_query(
        """
        SELECT a.resolved_at
        FROM alerts a JOIN alert_rules r ON r.id = a.rule_id
        WHERE r.slug = %(slug)s
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        {"slug": slug},
    )
    return rows[0]["resolved_at"] if rows else None


def _stamp_schedule_enabled(db, hours_ago):
    """Pin how long the schedule has been switched on, in BOTH places
    check_stale looks for that answer.

    With no successful run to measure from, check_stale anchors on when a
    success first became expected — the earlier of the first run attempt and
    the switch-on — and asks settings_audit_log for the switch-on first,
    falling back to system_settings.updated_at. Writing only one of the two
    leaves the anchor at whatever real audit history the database happens to
    carry, which is not a fixture.

    Stamped in SQL because both columns are `timestamp WITHOUT time zone` and
    need to land the way NOW() writes them — and because _set_setting's ON
    CONFLICT branch updates only `value`, leaving updated_at at whatever a
    previous run of this test left behind.
    """
    db.execute_query(
        """
        UPDATE system_settings
        SET updated_at = now() - make_interval(hours => %(hours)s)
        WHERE category = 'asset_generation'
          AND key = 'pipeline_schedule_enabled'
        """,
        {"hours": hours_ago},
        fetch=False,
    )
    # Scoped to this one setting: the rest of the audit trail is untouched.
    db.execute_query(
        """
        DELETE FROM settings_audit_log
        WHERE category = 'asset_generation'
          AND key = 'pipeline_schedule_enabled'
        """,
        fetch=False,
    )
    # A false->true transition, the shape settings_manager writes when an admin
    # switches the schedule on ('true'/'false' lowercase, per _serialize_value).
    db.execute_query(
        """
        INSERT INTO settings_audit_log
            (setting_id, category, key, old_value, new_value,
             changed_by, changed_by_username, created_at)
        SELECT id, category, key, 'false', 'true', 'test', 'test',
               now() - make_interval(hours => %(hours)s)
        FROM system_settings
        WHERE category = 'asset_generation'
          AND key = 'pipeline_schedule_enabled'
        """,
        {"hours": hours_ago},
        fetch=False,
    )


@pytest.fixture
def service(live_db):
    # Enable schedule with interval=1h so 2x cadence = 2h (easy to cross).
    # value column is TEXT; _get_setting json-parses it ('true' -> True, '1' -> 1, '"x"' -> "x").
    _set_setting(live_db, "asset_generation", "pipeline_schedule_enabled", "true", "boolean")
    _set_setting(live_db, "asset_generation", "pipeline_schedule_mode", '"interval"', "string")
    _set_setting(live_db, "asset_generation", "pipeline_schedule_interval_hours", "1", "integer")
    _set_setting(live_db, "asset_generation", "pipeline_max_expected_duration_minutes", "60", "integer")
    _stamp_schedule_enabled(live_db, hours_ago=0)

    _reset_state(live_db)
    return PipelineWatchdogService(
        watchdog_repo=PipelineWatchdogRepository(live_db),
        alert_repo=AlertsRepository(live_db),
    )


@pytest.mark.integration
def test_state_machine_walk_through(service, live_db):
    # --- stale: a schedule enabled just now is not overdue yet ---
    # Day one of an install. Nothing has run because nothing was due, so a
    # 'critical' here would notify every routed channel about a healthy system.
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-stale") == 0

    # --- stale: no success 5 h after enabling ---
    # A run was due one interval (1 h) after the switch-on and none has ever
    # succeeded, so the alert is owed from the 60-minute grace onwards.
    _stamp_schedule_enabled(live_db, hours_ago=5)
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-stale") == 1

    # tick again -> still one active alert (dedup)
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-stale") == 1

    # --- stale resolves once a fresh successful run lands ---
    now = datetime.now(timezone.utc)
    _insert_run(live_db, status="completed",
                started_at=now - timedelta(minutes=10),
                completed_at=now - timedelta(minutes=5))
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-stale") == 0
    assert _resolved_at(live_db, "pipeline-stale") is not None

    # --- last-run-failed fires when the most recent finished run failed ---
    _insert_run(live_db, status="failed",
                started_at=now - timedelta(minutes=3),
                completed_at=now - timedelta(minutes=2),
                error_message="boom")
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-last-run-failed") == 1

    # ... and resolves when a newer successful run lands
    _insert_run(live_db, status="completed",
                started_at=now - timedelta(seconds=60),
                completed_at=now - timedelta(seconds=30))
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-last-run-failed") == 0

    # --- stuck fires for a run in 'running' longer than max_duration_minutes ---
    long_running = _insert_run(
        live_db, status="running",
        started_at=now - timedelta(minutes=120),
        created_at=now - timedelta(minutes=120),
    )
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-stuck") == 1

    # ... and resolves when the stuck run finally completes
    live_db.execute_query(
        """
        UPDATE pipeline_runs
        SET status = 'completed', completed_at = now()
        WHERE id = %(id)s::uuid
        """,
        {"id": long_running},
        fetch=False,
    )
    service.tick()
    assert _count_active_alerts(live_db, "pipeline-stuck") == 0
