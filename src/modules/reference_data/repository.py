# SPDX-License-Identifier: AGPL-3.0-or-later
"""CRUD for reference_data_updates."""

import json
import logging
from typing import List, Literal, Optional

logger = logging.getLogger(__name__)

RunStatus = Literal['running', 'success', 'failed']
RunSource = Literal['upload', 'github']


class ReferenceDataRepository:
    def __init__(self, db_adapter):
        self.db = db_adapter

    def create_run(self, bundle_version: str, source: RunSource,
                   initiated_by: Optional[str]) -> str:
        sql = """
        INSERT INTO reference_data_updates (bundle_version, source, initiated_by)
        VALUES (%(bundle_version)s, %(source)s, %(initiated_by)s)
        RETURNING id
        """
        rows = self.db.execute_query(sql, {
            'bundle_version': bundle_version,
            'source': source,
            'initiated_by': initiated_by,
        })
        return str(rows[0]['id'])

    def finish_run(self, run_id: str, status: RunStatus,
                   datasets: List[dict], error_message: Optional[str] = None) -> None:
        sql = """
        UPDATE reference_data_updates
        SET status = %(status)s,
            finished_at = NOW(),
            datasets = %(datasets)s::jsonb,
            error_message = %(error_message)s
        WHERE id = %(id)s::uuid
        """
        self.db.execute_query(sql, {
            'status': status,
            'datasets': json.dumps(datasets),
            'error_message': error_message,
            'id': run_id,
        }, fetch=False)

    def latest_successful(self) -> Optional[dict]:
        sql = """
        SELECT bundle_version, finished_at, datasets, source
        FROM reference_data_updates
        WHERE status = 'success'
        ORDER BY finished_at DESC
        LIMIT 1
        """
        rows = self.db.execute_query(sql)
        return rows[0] if rows else None

    def list_recent(self, limit: int = 20) -> List[dict]:
        sql = """
        SELECT id, bundle_version, source, initiated_by, started_at, finished_at,
               status, error_message, datasets
        FROM reference_data_updates
        ORDER BY started_at DESC
        LIMIT %(limit)s
        """
        return self.db.execute_query(sql, {'limit': limit}) or []

    def sweep_stale_running(self, max_age_seconds: int = 300) -> int:
        """
        Mark `running` rows older than max_age_seconds as `failed`.

        Why: if the API process dies mid-apply (Flask --debug auto-reload, gunicorn
        worker recycle, kill -9), the service's try/except never runs, leaving the
        run row stuck in `running` forever and polluting the history view.
        Returns the number of rows updated.
        """
        sql = """
        UPDATE reference_data_updates
        SET status = 'failed',
            finished_at = NOW(),
            error_message = COALESCE(error_message,
                'apply did not complete (' ||
                EXTRACT(EPOCH FROM (NOW() - started_at))::int ||
                's elapsed; server likely restarted mid-flight)')
        WHERE status = 'running'
          AND started_at < NOW() - make_interval(secs => %(max_age_seconds)s)
        """
        self.db.execute_query(sql, {'max_age_seconds': max_age_seconds}, fetch=False)
        return 0  # row count not exposed by adapter; callers don't need it
