# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Technology Repository

Reads the DB-seeded integration_technologies catalog. Rows here represent
wizard tiles that have no direct Python connector class — data is ingested
"via SIEM/datalake" by reusing one of the transport connectors.
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from src.database.postgres_connection import get_connection_manager


logger = logging.getLogger(__name__)

# Falls through to a no-op check if the dir is absent (e.g. on a server that
# only ships the built UI bundle).
_LOGO_DIR = Path(__file__).resolve().parents[4] / 'ui' / 'prod' / 'public'


class IntegrationTechnologyRepository:
    """Repository for the integration_technologies catalog."""

    def __init__(self, db_url: Optional[str] = None, connection_manager=None):
        self.db_url = db_url or os.getenv('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL not set")
        self._connection_manager = connection_manager or get_connection_manager(self.db_url)

    @contextmanager
    def _get_connection(self):
        with self._connection_manager.get_connection() as conn:
            yield conn

    def list_technologies(self) -> List[Dict[str, Any]]:
        """
        Return every technology row shaped for the wizard catalog.

        Output dict matches the metadata shape produced by
        ``IntegrationRegistry.get_all_connectors()`` so the two lists merge
        cleanly. ``connector_role`` is always ``'technology'`` (only code
        connectors carry the ``'transport'`` role).
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, category, logo_path, direct_connector,
                           siem_passthrough_supported, default_transports, description
                    FROM integration_technologies
                    ORDER BY name
                """)
                rows = cur.fetchall()

        return [
            {
                'integration_type': row['id'],
                'display_name': row['name'],
                'category': row['category'],
                'description': row['description'] or '',
                'logo_url': row['logo_path'],
                'connector_role': 'technology',
                'direct_connector': row['direct_connector'],
                'siem_passthrough_supported': row['siem_passthrough_supported'],
                'default_transports': list(row['default_transports'] or []),
                'config_schema': {},
            }
            for row in rows
        ]

    def warn_missing_logos(
        self, technologies: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """
        Log a warning for every technology row whose logo_path is missing on
        disk. Returns the list of missing logo_paths (empty when all present
        or the public assets dir is unavailable).

        Pass ``technologies`` to reuse an already-fetched list and skip the DB
        roundtrip; otherwise the method queries the catalog itself.
        """
        if not _LOGO_DIR.exists():
            return []

        if technologies is None:
            technologies = self.list_technologies()

        missing: List[str] = []
        for tech in technologies:
            logo_url = tech.get('logo_url') or ''
            if not logo_url.startswith('/'):
                continue
            disk_path = _LOGO_DIR / logo_url.lstrip('/')
            if not disk_path.exists():
                logger.warning(
                    "Integration technology '%s' has missing logo: %s",
                    tech['integration_type'], logo_url,
                )
                missing.append(logo_url)
        return missing
