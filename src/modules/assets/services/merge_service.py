# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Merge Service

Handles merging two asset records into one. The "primary" asset survives
with combined fields; the "secondary" is soft-deleted via merged_into.
Supports two strategies: prefer_primary and prefer_latest.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)

# Fields stored as PostgreSQL arrays — always unioned during merge
ARRAY_FIELDS = [
    "ip",
    "mac",
    "data_source",
    "sn",
    "hostname",
    "fqdn",
    "manufacturer",
    "model",
    "mac_vendor_enriched",
    "owner",
    "location",
    "user_field",
    "ipam_location",
]

# Scalar fields — resolved by merge strategy
SCALAR_FIELDS = [
    "category",
    "subcategory",
    "os_normalized",
    "confidence",
]

VALID_STRATEGIES = ("prefer_primary", "prefer_latest")


class MergeService:
    """Service for merging two asset records."""

    def __init__(self, db_adapter: PostgreSQLAdapter, label_override_repo=None):
        self.db = db_adapter
        self.label_override_repo = label_override_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preview_merge(
        self, primary_id: str, secondary_id: str
    ) -> Dict[str, Any]:
        """
        Preview the result of merging two assets without persisting.

        Args:
            primary_id: UUID of the asset that will survive.
            secondary_id: UUID of the asset that will be absorbed.

        Returns:
            Dict with primary, secondary, and preview keys.

        Raises:
            LookupError: If either asset is not found.
            ValueError: If either asset has already been merged.
        """
        primary = self._fetch_asset(primary_id)
        secondary = self._fetch_asset(secondary_id)

        if primary.get("merged_into") is not None:
            raise ValueError(
                f"Primary asset {primary_id} has already been merged"
            )
        if secondary.get("merged_into") is not None:
            raise ValueError(
                f"Secondary asset {secondary_id} has already been merged"
            )

        # Build a preview using prefer_primary as the default strategy
        merged, _ = self._resolve_fields(primary, secondary, "prefer_primary")

        return {
            "primary": primary,
            "secondary": secondary,
            "preview": merged,
        }

    def execute_merge(
        self,
        primary_id: str,
        secondary_id: str,
        strategy: str,
        user: str,
    ) -> Dict[str, Any]:
        """
        Merge secondary asset into primary.

        Args:
            primary_id: UUID of the surviving asset.
            secondary_id: UUID of the absorbed asset.
            strategy: One of "prefer_primary" or "prefer_latest".
            user: Username performing the merge.

        Returns:
            Dict with merged_asset and removed_asset_id.

        Raises:
            ValueError: If same asset, invalid strategy, or already merged.
        """
        if primary_id == secondary_id:
            raise ValueError("Cannot merge the same asset with itself")

        if strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy '{strategy}'. "
                f"Valid strategies: {list(VALID_STRATEGIES)}"
            )

        primary = self._fetch_asset(primary_id)
        secondary = self._fetch_asset(secondary_id)

        if primary.get("merged_into") is not None:
            raise ValueError(
                f"Primary asset {primary_id} has already been merged"
            )
        if secondary.get("merged_into") is not None:
            raise ValueError(
                f"Secondary asset {secondary_id} has already been merged"
            )

        now = datetime.now(timezone.utc)
        merged, resolutions = self._resolve_fields(primary, secondary, strategy)

        # 1. Update primary asset with merged fields
        self._update_primary_asset(primary_id, merged, now)

        # 2. Soft-delete secondary by setting merged_into
        self._set_merged_into(secondary_id, primary_id)

        # 3. Reassign asset_id_mappings from secondary → primary
        #    so the pipeline preserves the merge on next run
        original_mappings = self._get_mappings(secondary_id)
        self._reassign_mappings(secondary_id, primary_id)

        # 4. Insert audit record into asset_merges (includes snapshots for undo)
        #    Store both the original mappings AND the primary's pre-merge array values
        primary_arrays_snapshot = {
            field: primary.get(field) or [] for field in ARRAY_FIELDS
        }
        label_resolution = self._resolve_label_override(primary_id, secondary_id)
        if label_resolution is not None:
            resolutions = dict(resolutions or {})
            resolutions["asset_label_override"] = label_resolution
        self._insert_merge_record(
            primary_id, secondary_id, strategy, user, now, resolutions,
            original_mappings=original_mappings,
            primary_arrays_snapshot=primary_arrays_snapshot,
        )

        # 5. Chronicle events on both assets
        self.db.bulk_write_chronicle_events([
            {
                "time": now,
                "asset_id": primary_id,
                "event_type": "merged",
                "changed_attributes": list(resolutions.keys()),
                "attribute_changes": {
                    "merge": {"merged_from": secondary_id, "strategy": strategy}
                },
                "causation": f"Asset merge by {user}",
                "related_asset_id": secondary_id,
            },
            {
                "time": now + timedelta(microseconds=1),  # avoid ON CONFLICT
                "asset_id": secondary_id,
                "event_type": "merged_into",
                "attribute_changes": {
                    "merge": {"merged_into": primary_id, "strategy": strategy}
                },
                "causation": f"Asset merge by {user}",
                "related_asset_id": primary_id,
            },
        ])

        logger.info(
            f"Asset {secondary_id} merged into {primary_id} "
            f"(strategy={strategy}) by {user}"
        )

        return {
            "merged_asset": {
                "id": primary_id,
                "hostname": merged.get("hostname", []),
                "merged_from": secondary_id,
                "merged_at": now.isoformat(),
                "merged_by": user,
            },
            "removed_asset_id": secondary_id,
        }

    def unmerge(
        self, primary_id: str, secondary_id: str, user: str
    ) -> Dict[str, Any]:
        """
        Undo a previous merge by restoring the secondary asset.

        Steps:
        1. Look up the merge record in asset_merges.
        2. Remove the secondary's array values from the primary asset.
        3. Clear merged_into on the secondary asset.
        4. Restore original asset_id_mappings from the stored snapshot.
        5. Chronicle the unmerge on both assets.

        Args:
            primary_id: UUID of the primary (surviving) asset.
            secondary_id: UUID of the secondary (absorbed) asset.
            user: Username performing the unmerge.

        Returns:
            Dict with restored_asset_id and primary_asset_id.

        Raises:
            LookupError: If merge record or assets not found.
            ValueError: If the secondary is not currently merged into primary.
        """
        # 1. Fetch merge record
        merge_record = self._get_merge_record(primary_id, secondary_id)
        if not merge_record:
            raise LookupError(
                f"No merge record found for {secondary_id} → {primary_id}"
            )

        # Verify secondary is actually merged into primary
        primary = self._fetch_asset(primary_id)
        secondary = self._fetch_asset(secondary_id)
        if str(secondary.get("merged_into") or "") != primary_id:
            raise ValueError(
                f"Asset {secondary_id} is not currently merged into {primary_id}"
            )

        now = datetime.now(timezone.utc)

        # 2. Restore primary's original array values
        record_data = merge_record.get("field_resolutions") or {}
        primary_snapshot = record_data.get("primary_arrays_snapshot")
        self._restore_primary_arrays(primary_id, primary, secondary, primary_snapshot)
        self._restore_label_override(record_data, primary_id, secondary_id)

        # 3. Clear merged_into on secondary
        query = """
            UPDATE assets
            SET merged_into = NULL
            WHERE id = %s::uuid
        """
        self.db.execute_query(query, (secondary_id,), fetch=False)

        # 4. Restore original mappings
        record_data = merge_record.get("field_resolutions") or {}
        original_mappings = record_data.get("original_mappings", [])
        self._restore_mappings(original_mappings, secondary_id)

        # 5. Chronicle events
        self.db.bulk_write_chronicle_events([
            {
                "time": now,
                "asset_id": primary_id,
                "event_type": "unmerged",
                "attribute_changes": {
                    "unmerge": {"restored_asset": secondary_id}
                },
                "causation": f"Asset unmerge by {user}",
                "related_asset_id": secondary_id,
            },
            {
                "time": now + timedelta(microseconds=1),
                "asset_id": secondary_id,
                "event_type": "unmerged_from",
                "attribute_changes": {
                    "unmerge": {"unmerged_from": primary_id}
                },
                "causation": f"Asset unmerge by {user}",
                "related_asset_id": primary_id,
            },
        ])

        # 5. Delete the merge record (or mark it undone)
        self._delete_merge_record(merge_record["id"])

        logger.info(
            f"Asset {secondary_id} unmerged from {primary_id} by {user}"
        )

        return {
            "restored_asset_id": secondary_id,
            "primary_asset_id": primary_id,
            "unmerged_at": now.isoformat(),
            "unmerged_by": user,
        }

    def get_merge_history(self, asset_id: str) -> List[Dict[str, Any]]:
        """
        Get merge history for an asset (both as primary and secondary).

        Args:
            asset_id: Asset UUID.

        Returns:
            List of merge records with timestamps and details.
        """
        query = """
            SELECT
                am.id,
                am.primary_asset_id::text,
                am.secondary_asset_id::text,
                am.merge_strategy,
                am.merged_by,
                am.merged_at,
                am.field_resolutions,
                pa.hostname as primary_hostname,
                sa.hostname as secondary_hostname
            FROM asset_merges am
            LEFT JOIN assets pa ON am.primary_asset_id = pa.id
            LEFT JOIN assets sa ON am.secondary_asset_id = sa.id
            WHERE am.primary_asset_id = %s::uuid
               OR am.secondary_asset_id = %s::uuid
            ORDER BY am.merged_at DESC
        """
        results = self.db.execute_query(
            query, (asset_id, asset_id), fetch=True
        )
        if not results:
            return []

        history = []
        for row in results:
            primary_hostnames = row.get("primary_hostname") or []
            secondary_hostnames = row.get("secondary_hostname") or []
            history.append({
                "id": str(row["id"]),
                "primary_asset_id": row["primary_asset_id"],
                "secondary_asset_id": row["secondary_asset_id"],
                "primary_hostname": primary_hostnames[0] if primary_hostnames else None,
                "secondary_hostname": secondary_hostnames[0] if secondary_hostnames else None,
                "merge_strategy": row["merge_strategy"],
                "merged_by": row["merged_by"],
                "merged_at": row["merged_at"].isoformat() if row.get("merged_at") else None,
                "role": "primary" if row["primary_asset_id"] == asset_id else "secondary",
            })

        return history

    # ------------------------------------------------------------------
    # Field resolution
    # ------------------------------------------------------------------

    def _resolve_fields(
        self,
        primary: Dict[str, Any],
        secondary: Dict[str, Any],
        strategy: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Merge fields from two asset records.

        Array fields are always unioned. Scalar fields are resolved by strategy.

        Returns:
            (merged_dict, resolutions_dict)
        """
        merged: Dict[str, Any] = {}
        resolutions: Dict[str, Any] = {}

        # Union array fields
        for field in ARRAY_FIELDS:
            a = primary.get(field) or []
            b = secondary.get(field) or []
            merged[field] = self._union_arrays(a, b)

        # Resolve scalar fields
        for field in SCALAR_FIELDS:
            p_val = primary.get(field)
            s_val = secondary.get(field)

            if strategy == "prefer_primary":
                chosen = p_val if p_val is not None else s_val
                source = "primary" if p_val is not None else "secondary"
            else:  # prefer_latest
                p_seen = primary.get("last_seen") or ""
                s_seen = secondary.get("last_seen") or ""
                if s_seen > p_seen:
                    chosen = s_val if s_val is not None else p_val
                    source = "secondary" if s_val is not None else "primary"
                else:
                    chosen = p_val if p_val is not None else s_val
                    source = "primary" if p_val is not None else "secondary"

            merged[field] = chosen
            resolutions[field] = {
                "value": chosen,
                "source": source,
                "primary_value": p_val,
                "secondary_value": s_val,
            }

        return merged, resolutions

    # ------------------------------------------------------------------
    # asset_label override carry-over (#639)
    # ------------------------------------------------------------------

    def _resolve_label_override(self, primary_id: str, secondary_id: str):
        """Decide what happens to asset_label overrides during a merge.

        Returns one of:
        - None — neither asset has an override.
        - {"primary_kept": <label>} — primary's override wins.
        - {"carried_over_from": <secondary_id>, "label": <label>} — secondary's
          override has been moved to the primary.
        """
        if not self.label_override_repo:
            return None

        primary = self.label_override_repo.get_override(primary_id)
        secondary = self.label_override_repo.get_override(secondary_id)

        if primary:
            return {"primary_kept": primary.get("label")}
        if secondary:
            self.label_override_repo.transfer_override(secondary_id, primary_id)
            return {
                "carried_over_from": secondary_id,
                "label": secondary.get("label"),
            }
        return None

    def _restore_label_override(self, field_resolutions, primary_id: str, secondary_id: str):
        """Reverse a carry-over recorded in field_resolutions.

        Only acts when carried_over_from matches; primary_kept and absent
        keys are no-ops.
        """
        if not self.label_override_repo or not field_resolutions:
            return
        info = (field_resolutions or {}).get("asset_label_override") or {}
        if info.get("carried_over_from") == secondary_id:
            self.label_override_repo.transfer_override(primary_id, secondary_id)

    # ------------------------------------------------------------------
    # Array helpers
    # ------------------------------------------------------------------

    def _union_arrays(self, a: List, b: List) -> List:
        """
        Union two lists, deduplicate, and preserve first-seen order.

        Items from *a* appear first (in their original order), followed by
        items from *b* that were not already in *a*.
        """
        seen = set()
        result = []
        for item in a:
            if item not in seen:
                seen.add(item)
                result.append(item)
        for item in b:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _fetch_asset(self, asset_id: str) -> Dict[str, Any]:
        """
        Fetch a single asset by ID.

        Raises:
            LookupError: If the asset does not exist.
        """
        query = """
            SELECT id, hostname, category, subcategory, ip, mac,
                   data_source, sn, os_normalized, fqdn, manufacturer,
                   model, mac_vendor_enriched, confidence, owner, location,
                   last_seen, merged_into
            FROM assets
            WHERE id = %s::uuid
        """
        results = self.db.execute_query(query, (asset_id,), fetch=True)
        if not results:
            raise LookupError(f"Asset not found: {asset_id}")
        return results[0]

    def _update_primary_asset(
        self,
        asset_id: str,
        merged: Dict[str, Any],
        updated_at: datetime,
    ) -> None:
        """Update the primary asset with merged field values."""
        query = """
            UPDATE assets
            SET hostname = %s,
                ip = %s,
                mac = %s,
                data_source = %s,
                sn = %s,
                fqdn = %s,
                manufacturer = %s,
                model = %s,
                mac_vendor_enriched = %s,
                category = %s,
                subcategory = %s,
                os_normalized = %s,
                confidence = %s,
                owner = %s,
                location = %s,
                updated_at = %s
            WHERE id = %s::uuid
        """
        self.db.execute_query(
            query,
            (
                merged.get("hostname"),
                merged.get("ip"),
                merged.get("mac"),
                merged.get("data_source"),
                merged.get("sn"),
                merged.get("fqdn"),
                merged.get("manufacturer"),
                merged.get("model"),
                merged.get("mac_vendor_enriched"),
                merged.get("category"),
                merged.get("subcategory"),
                merged.get("os_normalized"),
                merged.get("confidence"),
                merged.get("owner"),
                merged.get("location"),
                updated_at,
                asset_id,
            ),
            fetch=False,
        )

    def _set_merged_into(self, secondary_id: str, primary_id: str) -> None:
        """Soft-delete secondary asset by setting merged_into."""
        query = """
            UPDATE assets
            SET merged_into = %s::uuid
            WHERE id = %s::uuid
        """
        self.db.execute_query(query, (primary_id, secondary_id), fetch=False)

    def _restore_primary_arrays(
        self,
        primary_id: str,
        primary: Dict[str, Any],
        secondary: Dict[str, Any],
        snapshot: Optional[Dict[str, List]] = None,
    ) -> None:
        """
        Restore the primary asset's array fields to their pre-merge state.

        If a snapshot of the original values exists (from new-style merge
        records), use it directly. Otherwise fall back to subtracting the
        secondary's values from the primary.
        """
        if snapshot:
            # Exact restore from snapshot
            restored = {field: snapshot.get(field, []) for field in ARRAY_FIELDS}
        else:
            # Fallback: subtract secondary values from primary
            restored = {}
            for field in ARRAY_FIELDS:
                p_vals = primary.get(field) or []
                s_vals = set(v for v in (secondary.get(field) or []) if v is not None)
                restored[field] = [v for v in p_vals if v not in s_vals]

        query = """
            UPDATE assets
            SET hostname = %s,
                ip = %s,
                mac = %s,
                data_source = %s,
                sn = %s,
                fqdn = %s,
                manufacturer = %s,
                model = %s,
                mac_vendor_enriched = %s,
                updated_at = NOW()
            WHERE id = %s::uuid
        """
        self.db.execute_query(
            query,
            (
                restored.get("hostname"),
                restored.get("ip"),
                restored.get("mac"),
                restored.get("data_source"),
                restored.get("sn"),
                restored.get("fqdn"),
                restored.get("manufacturer"),
                restored.get("model"),
                restored.get("mac_vendor_enriched"),
                primary_id,
            ),
            fetch=False,
        )

    def _get_mappings(self, asset_id: str) -> List[Dict[str, str]]:
        """Fetch all asset_id_mappings rows for a given asset."""
        query = """
            SELECT component, asset_id::text
            FROM asset_id_mappings
            WHERE asset_id = %s::uuid
        """
        results = self.db.execute_query(query, (asset_id,), fetch=True)
        return results if results else []

    def _reassign_mappings(
        self, secondary_id: str, primary_id: str
    ) -> int:
        """
        Reassign all asset_id_mappings from secondary → primary.

        This ensures the pipeline will assign the secondary's components
        to the primary asset on the next run, preserving the merge.

        Returns:
            Number of mappings reassigned.
        """
        query = """
            UPDATE asset_id_mappings
            SET asset_id = %s::uuid, updated_at = NOW()
            WHERE asset_id = %s::uuid
        """
        self.db.execute_query(
            query, (primary_id, secondary_id), fetch=False
        )
        # Return the count from original mappings (already fetched before call)
        return 0  # caller already has the count

    def _restore_mappings(
        self, original_mappings: List[Dict[str, str]], asset_id: str
    ) -> None:
        """
        Restore original asset_id_mappings for an unmerge operation.

        Updates mappings that were reassigned back to the original asset_id.
        """
        if not original_mappings:
            return

        components = [m["component"] for m in original_mappings]
        # Use ANY to match all components in one query
        query = """
            UPDATE asset_id_mappings
            SET asset_id = %s::uuid, updated_at = NOW()
            WHERE component = ANY(%s)
              AND asset_id != %s::uuid
        """
        self.db.execute_query(
            query, (asset_id, components, asset_id), fetch=False
        )

    def _insert_merge_record(
        self,
        primary_id: str,
        secondary_id: str,
        strategy: str,
        user: str,
        merged_at: datetime,
        field_resolutions: Dict[str, Any],
        original_mappings: Optional[List[Dict[str, str]]] = None,
        primary_arrays_snapshot: Optional[Dict[str, List]] = None,
    ) -> None:
        """Insert an audit row into asset_merges."""
        # Store snapshots alongside field resolutions for undo support
        record_data = {
            "field_resolutions": field_resolutions,
            "original_mappings": [
                {"component": m["component"], "asset_id": m["asset_id"]}
                for m in (original_mappings or [])
            ],
            "primary_arrays_snapshot": primary_arrays_snapshot or {},
        }
        query = """
            INSERT INTO asset_merges
                (primary_asset_id, secondary_asset_id, merge_strategy,
                 merged_by, merged_at, field_resolutions)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s::jsonb)
        """
        self.db.execute_query(
            query,
            (
                primary_id,
                secondary_id,
                strategy,
                user,
                merged_at,
                json.dumps(record_data, default=str),
            ),
            fetch=False,
        )

    def _get_merge_record(
        self, primary_id: str, secondary_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a merge record by primary and secondary asset IDs."""
        query = """
            SELECT id, primary_asset_id, secondary_asset_id,
                   merge_strategy, merged_by, merged_at, field_resolutions
            FROM asset_merges
            WHERE primary_asset_id = %s::uuid
              AND secondary_asset_id = %s::uuid
            ORDER BY merged_at DESC
            LIMIT 1
        """
        results = self.db.execute_query(
            query, (primary_id, secondary_id), fetch=True
        )
        return results[0] if results else None

    def _delete_merge_record(self, merge_id: str) -> None:
        """Delete a merge record by ID."""
        query = "DELETE FROM asset_merges WHERE id = %s::uuid"
        self.db.execute_query(query, (merge_id,), fetch=False)
