# SPDX-License-Identifier: AGPL-3.0-or-later
"""AssetLabelOverrideService — set / clear / read the per-asset manual
override of the asset_label virtual field.

Writes both:
- assets.asset_label_override (so the virtual SQL reads it without a join)
- asset_label_overrides table (survives TRUNCATE assets CASCADE)

Audit-logs via chronicle events (matches ReclassifyService).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.repositories.asset_label_override_repository import (
    AssetLabelOverrideRepository,
)

logger = logging.getLogger(__name__)


class AssetLabelOverrideService:
    def __init__(
        self,
        db_adapter: PostgreSQLAdapter,
        override_repo: AssetLabelOverrideRepository,
    ):
        self.db = db_adapter
        self.override_repo = override_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_override(
        self,
        asset_id: str,
        label: str,
        user: str,
    ) -> Dict[str, Any]:
        if label is None or not str(label).strip():
            raise ValueError("label must be a non-empty string")
        label = str(label).strip()

        current = self._get_current_asset(asset_id)
        if not current:
            raise LookupError(f"Asset not found: {asset_id}")

        previous = current.get("asset_label_override")
        now = datetime.now(timezone.utc)

        # Persist to the survives-truncate table FIRST. If this fails, the
        # assets column stays untouched — no partial-write window.
        if not self.override_repo.save_override(asset_id, label, overridden_by=user):
            raise RuntimeError("Failed to persist asset_label override")

        self._update_asset_column(asset_id, label, now)

        self._write_chronicle(
            asset_id, "manual_label_override_set", "set", previous, label, user, now,
        )

        logger.info(
            f"Asset {asset_id} label override set: {previous!r} -> {label!r} by {user}"
        )
        return {
            "id": asset_id,
            "label": label,
            "previous_label": previous,
            "overridden_by": user,
            "updated_at": now.isoformat(),
        }

    def clear_override(self, asset_id: str, user: str) -> Dict[str, Any]:
        current = self._get_current_asset(asset_id)
        if not current:
            raise LookupError(f"Asset not found: {asset_id}")

        previous = current.get("asset_label_override")
        now = datetime.now(timezone.utc)

        # Mark the survives-truncate row inactive FIRST. If this fails, the
        # assets column stays populated and the next pipeline run re-applies
        # the override (consistent behavior under partial failure). Reversing
        # this order would leave the assets column NULL but the override row
        # active — so the next pipeline run would silently "un-clear".
        if not self.override_repo.clear_override(asset_id):
            raise RuntimeError("Failed to deactivate asset_label override")

        self._update_asset_column(asset_id, None, now)

        self._write_chronicle(
            asset_id, "manual_label_override_cleared", "cleared", previous, None, user, now,
        )

        logger.info(
            f"Asset {asset_id} label override cleared (previous={previous!r}) by {user}"
        )
        return {
            "id": asset_id,
            "label": None,
            "previous_label": previous,
            "overridden_by": user,
            "updated_at": now.isoformat(),
        }

    def get_override(self, asset_id: str) -> Optional[Dict[str, Any]]:
        return self.override_repo.get_override(asset_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_current_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_query(
            "SELECT id, asset_label_override FROM assets WHERE id = %s::uuid",
            (asset_id,),
            fetch=True,
        )
        return rows[0] if rows else None

    def _update_asset_column(
        self, asset_id: str, value: Optional[str], updated_at: datetime,
    ) -> None:
        self.db.execute_query(
            """
            UPDATE assets
            SET asset_label_override = %s,
                updated_at = %s
            WHERE id = %s::uuid
            """,
            (value, updated_at, asset_id),
            fetch=False,
        )

    def _write_chronicle(
        self,
        asset_id: str,
        event_type: str,
        verb: str,
        old: Optional[str],
        new: Optional[str],
        user: str,
        ts: datetime,
    ) -> None:
        self.db.bulk_write_chronicle_events([{
            "time": ts,
            "asset_id": asset_id,
            "event_type": event_type,
            "changed_attributes": ["asset_label_override"],
            "attribute_changes": {
                "asset_label_override": {"old": old, "new": new},
            },
            "causation": f"Asset label override {verb} by {user}",
        }])
