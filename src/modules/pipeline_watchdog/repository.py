# SPDX-License-Identifier: AGPL-3.0-or-later
"""PipelineWatchdogRepository — direct SQL reads for the watchdog.

No writes here. Alert writes go through AlertsRepository (reused as-is).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.api.modules.settings.settings_schema import get_default_value
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.pipeline_watchdog.domain import ScheduleSettings

logger = logging.getLogger(__name__)


class PipelineWatchdogRepository:
    """Reads pipeline_runs and pipeline-schedule settings for the watchdog."""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        self.db = db_adapter

    # -----------------------------------------------------------------
    # pipeline_runs
    # -----------------------------------------------------------------

    def last_successful_completed_at(self) -> Optional[datetime]:
        """Timestamp of the most recent successful completion, or None."""
        rows = self.db.execute_query(
            """
            SELECT MAX(completed_at) AS ts
            FROM pipeline_runs
            WHERE status = 'completed'
            """
        )
        return rows[0]["ts"] if rows and rows[0].get("ts") else None

    def first_run_created_at(self) -> Optional[datetime]:
        """Timestamp of the earliest pipeline run on record, or None.

        Deliberately unfiltered by status: this answers "when did this install
        first start expecting the pipeline to produce something", and a run
        that failed still proves the pipeline was being asked to do work.
        """
        rows = self.db.execute_query(
            """
            SELECT MIN(created_at) AS ts
            FROM pipeline_runs
            """
        )
        return rows[0]["ts"] if rows and rows[0].get("ts") else None

    def has_run_in_flight(self) -> bool:
        """True when any run is currently pending or running.

        Deliberately unbounded by duration, unlike stuck_runs(): check_stale
        only needs to know that the pipeline is working, and how long it has
        been at it is 'pipeline-stuck' business, at its own threshold and its
        own severity.
        """
        rows = self.db.execute_query(
            """
            SELECT 1 AS in_flight
            FROM pipeline_runs
            WHERE status IN ('pending', 'running')
            LIMIT 1
            """
        )
        return bool(rows)

    def last_finished_run(self) -> Optional[Dict[str, Any]]:
        """Most recent row with status IN ('completed', 'failed'), or None.

        Ordered by completed_at DESC so the newest finish wins regardless of
        which finished first.
        """
        rows = self.db.execute_query(
            """
            SELECT id, status, started_at, completed_at,
                   duration_seconds, error_message
            FROM pipeline_runs
            WHERE status IN ('completed', 'failed')
            ORDER BY completed_at DESC NULLS LAST
            LIMIT 1
            """
        )
        return rows[0] if rows else None

    def recover_orphaned_pending_runs(self, threshold_minutes: int) -> int:
        """Mark long-pending rows with no started_at as failed and return the count.

        trigger_run() blocks while any pending/running row exists, so a row left
        pending past the spawn window permanently wedges the pipeline.
        """
        rows = self.db.execute_query(
            """
            UPDATE pipeline_runs
            SET status = 'failed',
                completed_at = now(),
                error_message = %(msg)s
            WHERE status = 'pending'
              AND started_at IS NULL
              AND created_at < now() - make_interval(mins => %(threshold)s)
            RETURNING id
            """,
            {
                "msg": (
                    "Orphaned: pending without start beyond threshold; "
                    "worker thread likely crashed or process exited mid-trigger."
                ),
                "threshold": threshold_minutes,
            },
        )
        return len(rows or [])

    def recover_orphaned_running_runs(self, threshold_minutes: int) -> int:
        """Mark rows running longer than threshold_minutes as failed.

        The _execute_run() worker always finalises the row to completed/failed
        on its own exception path, so a row still in 'running' this long after
        started_at means the host process died (Flask reload, OOM, kill -9) and
        the daemon thread went with it. Wedges trigger_run() and leaves the UI
        showing a phantom "running" status until recovered.

        threshold_minutes must stay comfortably LARGER than the
        pipeline_max_expected_duration_minutes setting that the 'pipeline-stuck'
        alert fires on — see ORPHAN_RECOVERY_MULTIPLIER in service.py. This
        UPDATE rewrites the very rows check_stuck reads: passing the setting
        itself makes that alert permanently unfireable, and kills the row of a
        run that is merely slow (releasing trigger_run()'s concurrency guard
        while its worker is still writing).
        """
        rows = self.db.execute_query(
            """
            UPDATE pipeline_runs
            SET status = 'failed',
                completed_at = now(),
                duration_seconds = EXTRACT(EPOCH FROM (now() - started_at)),
                current_stage = NULL,
                error_message = %(msg)s
            WHERE status = 'running'
              AND started_at IS NOT NULL
              AND started_at < now() - make_interval(mins => %(threshold)s)
            RETURNING id
            """,
            {
                "msg": (
                    "Orphaned: still running beyond the recovery window "
                    "(a multiple of the max expected duration); worker thread "
                    "crashed or host process exited mid-run."
                ),
                "threshold": threshold_minutes,
            },
        )
        return len(rows or [])

    def stuck_runs(self, max_duration_minutes: int) -> List[Dict[str, Any]]:
        """Rows that are pending or running beyond max_duration_minutes.

        Age is measured from started_at when present, else from created_at
        (covers rows stuck in 'pending' that never transitioned to 'running').
        """
        rows = self.db.execute_query(
            """
            SELECT id, status, started_at, created_at,
                   EXTRACT(EPOCH FROM (now() - COALESCE(started_at, created_at)))::int
                       AS running_seconds
            FROM pipeline_runs
            WHERE status IN ('pending', 'running')
              AND COALESCE(started_at, created_at)
                  < now() - make_interval(mins => %(max_minutes)s)
            ORDER BY COALESCE(started_at, created_at) ASC
            """,
            {"max_minutes": max_duration_minutes},
        )
        return rows or []

    # -----------------------------------------------------------------
    # system_settings
    # -----------------------------------------------------------------

    def get_pipeline_schedule_settings(self) -> ScheduleSettings:
        """Snapshot of the pipeline-schedule settings, with schema fallback."""
        values = self._get_settings("asset_generation", [
            "pipeline_schedule_enabled",
            "pipeline_schedule_mode",
            "pipeline_schedule_interval_hours",
            "pipeline_schedule_cron",
            "pipeline_schedule_timezone",
        ])
        return ScheduleSettings(
            enabled=bool(values["pipeline_schedule_enabled"]),
            mode=values["pipeline_schedule_mode"],
            interval_hours=int(values["pipeline_schedule_interval_hours"]),
            cron=values["pipeline_schedule_cron"],
            timezone=values["pipeline_schedule_timezone"],
        )

    def schedule_enabled_since(self) -> Optional[datetime]:
        """When the pipeline_schedule_enabled setting was last written, as
        aware UTC. None when the row has never been written.

        TRAP: system_settings.updated_at is `timestamp WITHOUT time zone` (NOT
        NULL, DEFAULT now()) and settings_manager writes it with NOW(), so
        psycopg2 hands it back naive. Every other timestamp this module deals
        in is aware UTC, and check_stale does `now - anchor` with it — so the
        stamping happens here, once, rather than at the call site. Skip it and
        that subtraction raises "can't subtract offset-naive and offset-aware
        datetimes" inside a contained check, disabling the stale alert with
        nothing but a log line to show for it.
        """
        rows = self.db.execute_query(
            """
            SELECT updated_at FROM system_settings
            WHERE category = %(category)s AND key = %(key)s
            """,
            {"category": "asset_generation", "key": "pipeline_schedule_enabled"},
        )
        ts = rows[0]["updated_at"] if rows else None
        if ts is None:
            return None
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)

    def schedule_enabled_transition_at(self) -> Optional[datetime]:
        """When pipeline_schedule_enabled last went false -> true, as aware UTC.
        None when no such transition was ever logged.

        Preferred over schedule_enabled_since() because it is a real transition
        rather than the last time anything was written. settings_manager stamps
        system_settings.updated_at on EVERY write, including a re-save that
        changes nothing — so on an install with no pipeline_runs rows (nothing
        for first_run_created_at to shield the anchor with), an admin who opens
        the schedule form and presses Save moves the anchor to now. The firing
        stale alert resolves and re-fires a cadence later with a fresh outbound
        delivery: investigating the alert makes it vanish.

        The audit row carries old_value/new_value, so a re-save (true -> true)
        is distinguishable from a switch-on (false/NULL -> true). MAX() so a
        schedule toggled off and on again anchors on the latest switch-on.

        TRAP: settings_audit_log.created_at is `timestamp WITHOUT time zone`
        DEFAULT now(), exactly like system_settings.updated_at — psycopg2 hands
        it back naive. Stamp UTC here, once, because check_stale does
        `now - anchor` with it; skip it and that raises TypeError inside a
        contained check, disabling the stale alert with only a log line to show
        for it.
        """
        rows = self.db.execute_query(
            """
            SELECT MAX(created_at) AS ts
            FROM settings_audit_log
            WHERE category = %(category)s AND key = %(key)s
              AND new_value = 'true'
              AND old_value IS DISTINCT FROM 'true'
            """,
            {"category": "asset_generation", "key": "pipeline_schedule_enabled"},
        )
        # 'true'/'false' lowercase: settings_manager._serialize_value writes
        # booleans that way, into both system_settings.value and the audit row.
        ts = rows[0]["ts"] if rows else None
        if ts is None:
            return None
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)

    def get_max_expected_duration_minutes(self) -> int:
        values = self._get_settings(
            "asset_generation", ["pipeline_max_expected_duration_minutes"],
        )
        return int(values["pipeline_max_expected_duration_minutes"])

    def get_stale_grace_minutes(self) -> int:
        """Grace allowed after a scheduled run came due before calling it stale."""
        values = self._get_settings(
            "asset_generation", ["pipeline_stale_grace_minutes"],
        )
        return int(values["pipeline_stale_grace_minutes"])

    # -----------------------------------------------------------------
    # internal
    # -----------------------------------------------------------------

    def _get_settings(self, category: str, keys: List[str]) -> Dict[str, Any]:
        """Batch-read setting values for a category, falling back to schema defaults."""
        try:
            rows = self.db.execute_query(
                """
                SELECT key, value FROM system_settings
                WHERE category = %(category)s AND key = ANY(%(keys)s)
                """,
                {"category": category, "keys": keys},
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("pipeline_watchdog: failed to read %s settings", category)
            rows = []

        stored = {row["key"]: _parse_setting_value(row["value"]) for row in rows or []}
        return {key: stored.get(key, get_default_value(category, key)) for key in keys}


def _parse_setting_value(val: Any) -> Any:
    """system_settings.value is TEXT holding a JSON literal; parse or fall back to raw."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return val
    return val
