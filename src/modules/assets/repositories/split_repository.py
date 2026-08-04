# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import json
from typing import List

from src.database.postgres_adapter import PostgreSQLAdapter, _clean_nan


class AssetSplitRepository:
    def __init__(self, db: PostgreSQLAdapter):
        self.db = db

    def load_provider_records(self, asset_id: str) -> List[dict]:
        """Per-source records that formed this asset (one row = one record)."""
        sql = """
            SELECT hostname, fqdn, mac::text AS mac, sn
            FROM asset_providers
            WHERE asset_id = %(asset_id)s
        """
        return self.db.execute_query(sql, {"asset_id": asset_id}) or []

    def delete_mappings(self, components: List[str]) -> int:
        """Delete asset_id_mappings rows for the given components. Idempotent."""
        if not components:
            return 0
        sql = "DELETE FROM asset_id_mappings WHERE component = ANY(%(components)s) RETURNING component"
        rows = self.db.execute_query(sql, {"components": components})
        return len(rows or [])

    def insert_split_history(
        self,
        *,
        split_from: str,
        split_into: List[str],
        component_distribution: dict,
        reason: str,
        performed_by: str,
    ) -> None:
        sql = """
            INSERT INTO split_history
                (split_from, split_into, split_reason, component_distribution,
                 performed_by, prevent_auto_remerge)
            VALUES
                (%(split_from)s::uuid, %(split_into)s::uuid[], %(reason)s,
                 %(dist)s::jsonb, %(by)s, true)
        """
        self.db.execute_query(
            sql,
            {
                "split_from": split_from,
                "split_into": split_into,
                "reason": reason,
                "dist": json.dumps(_clean_nan(component_distribution)),
                "by": performed_by,
            },
            fetch=False,
        )
