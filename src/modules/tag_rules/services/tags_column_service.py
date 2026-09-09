# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Service for refreshing the denormalized tags JSONB column on the assets table.

The tags column stores {"TagName": true, ...} for fast filtering.
Source of truth remains asset_tags_v2 (rule-based) and asset_manual_tags (manual).
"""

import logging
from typing import Dict, Any, List

from flask import has_request_context

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields.unified.materialize import outside_request_scope
from src.modules.shared import asset_data_version

logger = logging.getLogger(__name__)


def _bump_data_stamp_post_commit(db: PostgreSQLAdapter) -> None:
    """Bump the asset-data stamp AFTER the fleet-wide tags rewrite is durable.

    The rewrite changes every tag: field's distinct values, so the Explorer
    field-values cache must invalidate (#1410). On the request path
    (single-rule execute / execute-all routes) the UPDATE commits only at
    request teardown, so the bump runs inside outside_request_scope —
    entering it ends the request scope (the commit) first; a bump fired
    earlier would let a concurrent /values request read the new stamp,
    query the pre-write snapshot, and cache stale tag values under the new
    key until the next bump or the TTL.

    On the pipeline thread (s17 runs this inside the orchestrator's write
    scope) the scope must NOT be unwound — it is the run's atomic asset
    transaction — so the bump is skipped there; PipelineService bumps the
    stamp on every terminal run path. With no active scope (CLI/tests)
    statements auto-commit, so a direct bump is already post-commit.
    """
    if has_request_context():
        with outside_request_scope(db.conn_manager):
            asset_data_version.bump()
    elif not db.conn_manager.is_scope_active():
        asset_data_version.bump()


def refresh_tags_column(db: PostgreSQLAdapter) -> Dict[str, Any]:
    """
    Rebuild tags JSONB for ALL assets from asset_tags_v2 + asset_manual_tags.

    Called after execute_all_enabled_rules() in the pipeline.
    Uses a single UPDATE ... FROM subquery for efficiency.
    """
    query = """
        WITH tag_map AS (
            SELECT
                asset_id,
                jsonb_object_agg(tag_name, true) AS tags
            FROM (
                SELECT asset_id, tag_name FROM asset_tags_v2
                UNION
                SELECT asset_id, tag_name FROM asset_manual_tags
            ) all_tags
            GROUP BY asset_id
        )
        UPDATE assets a
        SET tags = COALESCE(tm.tags, '{}'::jsonb)
        FROM (SELECT id FROM assets) base
        LEFT JOIN tag_map tm ON tm.asset_id = base.id
        WHERE a.id = base.id
          AND a.tags IS DISTINCT FROM COALESCE(tm.tags, '{}'::jsonb);
    """
    try:
        db.execute_query(query, {}, fetch=False)
    except Exception as e:
        logger.error(f"Failed to refresh tags column: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}
    logger.info("Refreshed tags column for all assets")
    _bump_data_stamp_post_commit(db)
    return {"status": "success"}


def refresh_tags_for_assets(db: PostgreSQLAdapter, asset_ids: List[str]) -> None:
    """
    Rebuild tags JSONB for a LIST of assets in ONE statement.

    Sits between refresh_tags_for_asset (one UPDATE per asset) and
    refresh_tags_column (rewrites the whole fleet). A bulk manual-tag write
    touches up to the bulk-action cap of assets, where looping the single-asset
    version costs a round trip each and the all-assets version rebuilds rows the
    caller never touched.

    Raises on failure like refresh_tags_for_asset: callers run this inside the
    same transaction scope as the tag write, and swallowing the error would
    commit asset_manual_tags rows while leaving the denormalized column — what
    Explorer actually filters on — stale.
    """
    if not asset_ids:
        return

    query = """
        WITH tag_map AS (
            SELECT
                asset_id,
                jsonb_object_agg(tag_name, true) AS tags
            FROM (
                SELECT asset_id, tag_name FROM asset_tags_v2
                WHERE asset_id = ANY(%(ids)s::uuid[])
                UNION
                SELECT asset_id, tag_name FROM asset_manual_tags
                WHERE asset_id = ANY(%(ids)s::uuid[])
            ) all_tags
            GROUP BY asset_id
        )
        UPDATE assets a
        SET tags = COALESCE(tm.tags, '{}'::jsonb)
        FROM (SELECT id FROM assets WHERE id = ANY(%(ids)s::uuid[])) base
        LEFT JOIN tag_map tm ON tm.asset_id = base.id
        WHERE a.id = base.id
          AND a.tags IS DISTINCT FROM COALESCE(tm.tags, '{}'::jsonb);
    """
    try:
        db.execute_query(query, {"ids": list(asset_ids)}, fetch=False)
    except Exception as e:
        logger.error(
            f"Failed to refresh tags for {len(asset_ids)} assets: {e}", exc_info=True
        )
        raise


def refresh_tags_for_asset(db: PostgreSQLAdapter, asset_id: str) -> None:
    """
    Rebuild tags JSONB for a SINGLE asset.

    Called after manual tag add/remove operations.
    """
    query = """
        UPDATE assets
        SET tags = COALESCE(
            (
                SELECT jsonb_object_agg(tag_name, true)
                FROM (
                    SELECT tag_name FROM asset_tags_v2 WHERE asset_id = %(asset_id)s::uuid
                    UNION
                    SELECT tag_name FROM asset_manual_tags WHERE asset_id = %(asset_id)s::uuid
                ) all_tags
            ),
            '{}'::jsonb
        )
        WHERE id = %(asset_id)s::uuid;
    """
    try:
        db.execute_query(query, {"asset_id": asset_id}, fetch=False)
    except Exception as e:
        logger.error(f"Failed to refresh tags for asset {asset_id}: {e}", exc_info=True)
        raise
