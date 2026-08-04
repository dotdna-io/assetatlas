# SPDX-License-Identifier: AGPL-3.0-or-later
"""AssetLabelOverrideRepository — persistence layer for the asset_label
manual override.

Mirrors CategoryOverrideRepository: a separate table without FK to
assets so it survives the pipeline's TRUNCATE assets CASCADE.
"""

import logging
from typing import Dict, Optional
from uuid import UUID

import pandas as pd

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class AssetLabelOverrideRepository:
    """Repository for asset_label override CRUD and pipeline application."""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        self.db = db_adapter
        logger.info("AssetLabelOverrideRepository initialized")

    def get_all_overrides(self) -> Dict[str, str]:
        """Load active overrides as {asset_id_string: label}."""
        query = """
            SELECT asset_id, label
            FROM asset_label_overrides
            WHERE active = true
        """
        try:
            rows = self.db.execute_query(query, fetch=True) or []
            return {str(row["asset_id"]): row["label"] for row in rows}
        except Exception as exc:
            logger.error(f"Error loading asset_label overrides: {exc}")
            return {}

    def apply_overrides(
        self,
        df: pd.DataFrame,
        overrides: Dict[str, str],
    ) -> pd.DataFrame:
        """Set assets.asset_label_override on the in-memory DataFrame.

        Always creates the column (None for assets without an override) so
        the column shape is stable across pipeline runs.
        """
        if "id" not in df.columns:
            logger.warning("apply_overrides: 'id' column missing — skipping")
            return df

        if not overrides:
            df["asset_label_override"] = None
            return df

        mapped = df["id"].astype(str).map(overrides)
        df["asset_label_override"] = mapped.where(mapped.notna(), None)
        applied = df["asset_label_override"].notna().sum()
        logger.info(f"Applied {applied} asset_label overrides")
        return df

    def save_override(
        self,
        asset_id: UUID,
        label: str,
        overridden_by: Optional[str] = None,
    ) -> bool:
        """Upsert an override, marking it active."""
        query = """
            INSERT INTO asset_label_overrides (asset_id, label, overridden_by, active)
            VALUES (%s::uuid, %s, %s, true)
            ON CONFLICT (asset_id) DO UPDATE SET
                label         = EXCLUDED.label,
                overridden_by = EXCLUDED.overridden_by,
                active        = true,
                updated_at    = NOW()
        """
        try:
            self.db.execute_query(query, (str(asset_id), label, overridden_by), fetch=False)
            logger.info(f"Saved asset_label override for {asset_id}: {label!r}")
            return True
        except Exception as exc:
            logger.error(f"Error saving asset_label override: {exc}")
            return False

    def clear_override(self, asset_id: UUID) -> bool:
        """Deactivate the override (idempotent)."""
        query = """
            UPDATE asset_label_overrides
            SET active = false, updated_at = NOW()
            WHERE asset_id = %s::uuid
        """
        try:
            self.db.execute_query(query, (str(asset_id),), fetch=False)
            logger.info(f"Deactivated asset_label override for {asset_id}")
            return True
        except Exception as exc:
            logger.error(f"Error clearing asset_label override: {exc}")
            return False

    def get_override(self, asset_id: str) -> Optional[Dict]:
        """Return the active override row dict for asset_id, or None."""
        query = """
            SELECT asset_id, label, overridden_by, updated_at
            FROM asset_label_overrides
            WHERE asset_id = %s::uuid AND active = true
        """
        try:
            rows = self.db.execute_query(query, (str(asset_id),), fetch=True) or []
            return rows[0] if rows else None
        except Exception as exc:
            logger.error(f"Error reading asset_label override: {exc}")
            return None

    def transfer_override(self, from_asset_id: UUID, to_asset_id: UUID) -> bool:
        """Move an active override from one asset to another (used on merge).

        Deactivates the row on `from_asset_id` and upserts an active row on
        `to_asset_id` with the same label. Caller is responsible for
        deciding whether a transfer should happen.

        NOT atomic: between the upsert and the deactivate, both rows are
        briefly active. Acceptable here because merges are admin-initiated
        and sequential — if this turns into a hot path, fold both writes
        into a single CTE.
        """
        from_row = self.get_override(str(from_asset_id))
        if not from_row:
            return False
        if not self.save_override(
            to_asset_id,
            from_row["label"],
            overridden_by=from_row.get("overridden_by"),
        ):
            return False
        return self.clear_override(from_asset_id)
