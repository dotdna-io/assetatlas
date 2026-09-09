# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tag Service

Manages manual tags on assets, one at a time or across a resolved id set.
Handles add, bulk add, remove, and list operations against
the asset_manual_tags table, with chronicle event tracking.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields import catalog_version
from src.modules.fields.unified.materialize import outside_request_scope
from src.modules.shared import asset_data_version
from src.modules.tag_rules.services.tags_column_service import (
    refresh_tags_for_asset,
    refresh_tags_for_assets,
)

logger = logging.getLogger(__name__)


def _bump_field_catalog_stamp(db: PostgreSQLAdapter) -> None:
    """Bump the shared field-catalog and asset-data version stamps (#1413, #1410).

    asset_manual_tags feeds the catalog's tag: entries (a UNION with
    asset_tags_v2), so a manual add/remove can introduce or retire a tag
    field (catalog stamp), and it rewrites the denormalised assets.tags map
    the tag fields read, so the distinct values change too (data stamp).
    bump() never raises and is a no-op without Redis.

    The bumps run inside outside_request_scope: entering it ends the request
    scope, COMMITTING the tag write, so the new stamp is never visible to a
    concurrent /values request before the data it describes is. A bump fired
    while the writer's transaction is still open would let such a request
    read the new stamp, query the pre-write snapshot, and cache stale values
    under the new key until the next bump or the TTL. On a scope-free thread
    (CLI/tests) the wrapper is a no-op: statements auto-commit, so a direct
    bump is already post-commit.
    """
    with outside_request_scope(db.conn_manager):
        catalog_version.bump()
        asset_data_version.bump()


class TagService:
    """Service for per-asset manual tag operations."""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        self.db = db_adapter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_tag(
        self,
        asset_id: str,
        tag_name: str,
        tag_color: str,
        user: str,
    ) -> Dict[str, Any]:
        """
        Add a manual tag to an asset.

        Args:
            asset_id: Asset UUID string
            tag_name: Tag label
            tag_color: Hex colour (e.g. '#ff0000')
            user: Username performing the action

        Returns:
            Dict with the created/updated tag row

        Raises:
            ValueError: If tag_name is empty or blank
            LookupError: If the asset does not exist
        """
        if not tag_name or not tag_name.strip():
            raise ValueError("Tag name cannot be empty")

        # Verify asset exists
        asset_row = self.db.execute_query(
            "SELECT id FROM assets WHERE id = %s::uuid",
            (asset_id,),
            fetch=True,
        )
        if not asset_row:
            raise LookupError(f"Asset not found: {asset_id}")

        # Upsert tag (ON CONFLICT updates colour)
        query = """
            INSERT INTO asset_manual_tags (asset_id, tag_name, tag_color, created_by)
            VALUES (%s::uuid, %s, %s, %s)
            ON CONFLICT (asset_id, tag_name)
            DO UPDATE SET tag_color = EXCLUDED.tag_color
            RETURNING id, asset_id, tag_name, tag_color, created_by, created_at
        """
        rows = self.db.execute_query(
            query,
            (asset_id, tag_name.strip(), tag_color, user),
            fetch=True,
        )
        tag = rows[0]

        # Chronicle event
        self.db.bulk_write_chronicle_events([{
            "asset_id": asset_id,
            "event_type": "tag_added",
            "changed_attributes": ["manual_tags"],
            "attribute_changes": {"manual_tags": {"added": tag_name.strip()}},
            "causation": f"Manual tag added by {user}",
        }])

        # Refresh denormalized tags column
        refresh_tags_for_asset(self.db, asset_id)

        _bump_field_catalog_stamp(self.db)

        logger.info(
            f"Tag '{tag_name}' added to asset {asset_id} by {user}"
        )

        return tag

    def add_tag_bulk(
        self,
        asset_ids: List[str],
        tag_name: str,
        tag_color: str,
        user: str,
    ) -> Dict[str, Any]:
        """
        Apply one manual tag to many assets in a single transaction.

        Mirrors add_tag's conflict behaviour (an existing tag is recoloured) so
        the same tag applied per-asset and in bulk can't give two different
        results, but does it set-based: one upsert, one chronicle write, one
        tags-column refresh — not one round trip per asset.

        Args:
            asset_ids: Asset UUID strings (already resolved and capped by the caller)
            tag_name: Tag label
            tag_color: Hex colour (e.g. '#ff0000')
            user: Username performing the action

        Returns:
            Dict with 'tagged' (assets carrying the tag afterwards) and
            'created' (assets that did NOT already carry it)

        Raises:
            ValueError: If tag_name is empty or blank
        """
        name = (tag_name or "").strip()
        if not name:
            raise ValueError("Tag name cannot be empty")

        # ON CONFLICT DO UPDATE raises a cardinality violation if one statement
        # tries to touch the same row twice, so a duplicate in the caller's id
        # list would fail the whole batch. Dedupe, order-preserving.
        ids = list(dict.fromkeys(asset_ids or []))
        if not ids:
            return {"tagged": 0, "created": 0}

        now = datetime.now(timezone.utc)

        # xmax = 0 distinguishes a genuine INSERT from the DO UPDATE branch:
        # RETURNING fires for both, and without the split we would fabricate
        # 'tag_added' history for assets that already carried the tag.
        query = """
            INSERT INTO asset_manual_tags (asset_id, tag_name, tag_color, created_by)
            SELECT t.id, %(name)s, %(color)s, %(user)s
            FROM unnest(%(ids)s::uuid[]) AS t(id)
            ON CONFLICT (asset_id, tag_name)
            DO UPDATE SET tag_color = EXCLUDED.tag_color
            RETURNING asset_id, (xmax = 0) AS inserted
        """

        with self._bulk_scope():
            rows = self.db.execute_query(
                query,
                {"ids": ids, "name": name, "color": tag_color, "user": user},
                fetch=True,
            ) or []
            created_ids = [str(r["asset_id"]) for r in rows if r.get("inserted")]

            if created_ids:
                # One call for the batch — the per-asset path's single-element
                # list looped 1000 times would be 1000 round trips. The shared
                # 'time' gives the batch one timestamp.
                self.db.bulk_write_chronicle_events([
                    {
                        "time": now,
                        "asset_id": aid,
                        "event_type": "tag_added",
                        "changed_attributes": ["manual_tags"],
                        "attribute_changes": {"manual_tags": {"added": name}},
                        "causation": f"Bulk tag added by {user}",
                    }
                    for aid in created_ids
                ])

            # Explorer filters on the denormalized assets.tags map, not on
            # asset_manual_tags — skip this and the rows land but the tag never
            # becomes filterable.
            refresh_tags_for_assets(self.db, ids)

        _bump_field_catalog_stamp(self.db)

        logger.info(
            f"Tag '{name}' bulk-applied to {len(rows)} assets "
            f"({len(created_ids)} new) by {user}"
        )

        return {"tagged": len(rows), "created": len(created_ids)}

    def remove_tag_bulk(
        self,
        asset_ids: List[str],
        tag_name: str,
        user: str,
    ) -> Dict[str, Any]:
        """
        Remove one manual tag from many assets in a single transaction.

        The undo half of add_tag_bulk. Assets in the set that do not carry the
        tag simply do not match the DELETE — that is the expected case for a
        filter-scoped action, not an error.

        Note the asymmetry with add_tag_bulk: its upsert needs the (xmax = 0)
        split because RETURNING fires for both the INSERT and the DO UPDATE
        branch, whereas here every returned row is a genuine deletion.

        Args:
            asset_ids: Asset UUID strings (already resolved and capped by the caller)
            tag_name: Tag label to remove
            user: Username performing the action

        Returns:
            Dict with 'removed' (rows actually deleted)

        Raises:
            ValueError: If tag_name is empty or blank
        """
        name = (tag_name or "").strip()
        if not name:
            raise ValueError("Tag name cannot be empty")

        # Hygiene, not a correctness fix: unlike the upsert, a plain DELETE has
        # no cardinality-violation problem with a repeated id.
        ids = list(dict.fromkeys(asset_ids or []))
        if not ids:
            return {"removed": 0}

        now = datetime.now(timezone.utc)

        query = """
            DELETE FROM asset_manual_tags
            WHERE asset_id = ANY(%(ids)s::uuid[]) AND tag_name = %(name)s
            RETURNING asset_id
        """

        with self._bulk_scope():
            rows = self.db.execute_query(
                query,
                {"ids": ids, "name": name},
                fetch=True,
            ) or []
            removed_ids = [str(r["asset_id"]) for r in rows]

            if removed_ids:
                # One call for the batch, sharing one timestamp — and only for
                # the assets that actually carried the tag, so the skipped ones
                # get no fabricated 'tag_removed' history.
                self.db.bulk_write_chronicle_events([
                    {
                        "time": now,
                        "asset_id": aid,
                        "event_type": "tag_removed",
                        "changed_attributes": ["manual_tags"],
                        "attribute_changes": {"manual_tags": {"removed": name}},
                        "causation": f"Bulk tag removed by {user}",
                    }
                    for aid in removed_ids
                ])

            # Explorer filters on the denormalized assets.tags map, not on
            # asset_manual_tags — skip this and the rows are gone while the map
            # still advertises the tag on every asset.
            refresh_tags_for_assets(self.db, ids)

        _bump_field_catalog_stamp(self.db)

        logger.info(
            f"Tag '{name}' bulk-removed from {len(removed_ids)} assets by {user}"
        )

        return {"removed": len(removed_ids)}

    def get_bulk_tag_candidates(self, asset_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Manual tags carried by a resolved id set, with the count per tag.

        Populates the bulk-removal picker. Sourced from asset_manual_tags ONLY:
        assets.tags (and the field catalog built from it) is the union of the
        rule-applied asset_tags_v2 rows and the manual ones, and only manual
        rows can be deleted. Offering a rule-based tag would let the user pick
        something remove_tag_bulk can never match.

        Args:
            asset_ids: Asset UUID strings (already resolved and capped by the caller)

        Returns:
            List of {'tag_name', 'count'} dicts ordered by name; empty when no
            matched asset carries a manual tag.
        """
        ids = list(dict.fromkeys(asset_ids or []))
        if not ids:
            return []

        query = """
            SELECT tag_name, count(*)::int AS count
            FROM asset_manual_tags
            WHERE asset_id = ANY(%(ids)s::uuid[])
            GROUP BY tag_name
            ORDER BY tag_name
        """
        return self.db.execute_query(query, {"ids": ids}, fetch=True) or []

    def remove_tag(self, asset_id: str, tag_id: str) -> None:
        """
        Remove a manual tag from an asset.

        Args:
            asset_id: Asset UUID string
            tag_id: Tag row UUID string

        Raises:
            LookupError: If the tag does not exist
        """
        # Fetch tag info first
        tag_row = self.db.execute_query(
            "SELECT id, asset_id, tag_name, tag_color "
            "FROM asset_manual_tags WHERE id = %s::uuid AND asset_id = %s::uuid",
            (tag_id, asset_id),
            fetch=True,
        )
        if not tag_row:
            raise LookupError(f"Tag not found: {tag_id}")

        tag = tag_row[0]

        # Delete
        self.db.execute_query(
            "DELETE FROM asset_manual_tags WHERE id = %s::uuid",
            (tag_id,),
            fetch=False,
        )

        # Chronicle event
        self.db.bulk_write_chronicle_events([{
            "asset_id": asset_id,
            "event_type": "tag_removed",
            "changed_attributes": ["manual_tags"],
            "attribute_changes": {"manual_tags": {"removed": tag["tag_name"]}},
        }])

        # Refresh denormalized tags column
        refresh_tags_for_asset(self.db, asset_id)

        _bump_field_catalog_stamp(self.db)

        logger.info(
            f"Tag '{tag['tag_name']}' removed from asset {asset_id}"
        )

    def get_tags(self, asset_id: str) -> List[Dict[str, Any]]:
        """
        List all manual tags for an asset.

        Args:
            asset_id: Asset UUID string

        Returns:
            List of tag dicts, each with a 'source' = 'manual' field
        """
        query = """
            SELECT id, asset_id, tag_name, tag_color, created_by,
                   'manual' AS source
            FROM asset_manual_tags
            WHERE asset_id = %s::uuid
            ORDER BY created_at
        """
        return self.db.execute_query(query, (asset_id,), fetch=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _bulk_scope(self):
        """Run a block of bulk writes in one all-or-nothing transaction scope.

        Twin of ReclassifyService._bulk_scope — centralizes the
        begin/commit/rollback dance so the end_scope(error=...) rollback can't
        be forgotten on the failure path.
        """
        self.db.conn_manager.begin_scope()
        try:
            yield
        except Exception as exc:
            self.db.conn_manager.end_scope(error=exc)
            raise
        else:
            self.db.conn_manager.end_scope()
