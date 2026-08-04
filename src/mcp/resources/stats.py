# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stats resources for the AssetAtlas MCP server.

Resources: summary, data-sources
"""

import logging

from src.api.modules.settings.settings_schema import get_confidence_thresholds
from src.mcp.context import get_app_context

logger = logging.getLogger(__name__)


def register_resources(mcp):
    """Register stats resources on the FastMCP server."""

    @mcp.resource("assetatlas://stats/summary")
    def stats_summary() -> str:
        """Current asset counts, category breakdown, and quality metrics."""
        app_ctx = get_app_context()
        if not app_ctx:
            return "Database not available."

        db = app_ctx.db
        medium, high = get_confidence_thresholds(
            app_ctx.settings_manager if app_ctx else None
        )

        # Total assets
        totals = db.execute_query("""
            SELECT
                COUNT(*) as total_assets,
                COUNT(CASE WHEN confidence >= %(cl_high)s THEN 1 END) as high_confidence,
                COUNT(CASE WHEN confidence >= %(cl_med)s AND confidence < %(cl_high)s THEN 1 END) as medium_confidence,
                COUNT(CASE WHEN confidence < %(cl_med)s OR confidence IS NULL THEN 1 END) as low_confidence,
                ROUND(AVG(confidence)::numeric, 3) as avg_confidence
            FROM assets
        """, {"cl_high": high, "cl_med": medium})

        if not totals:
            return "No asset data available."

        t = totals[0]
        total = t["total_assets"]

        lines = ["AssetAtlas Summary\n"]
        lines.append(f"Total Assets: {total:,}")
        lines.append(f"Average Confidence: {t['avg_confidence'] or 0:.1%}")
        lines.append(f"  High (>={high:.0%}): {t['high_confidence']:,}")
        lines.append(f"  Medium ({medium:.0%}-{high:.0%}): {t['medium_confidence']:,}")
        lines.append(f"  Low (<{medium:.0%}): {t['low_confidence']:,}")

        # Category breakdown
        categories = db.execute_query("""
            SELECT
                COALESCE(category, 'Uncategorized') as category,
                COUNT(*) as count
            FROM assets
            GROUP BY category
            ORDER BY count DESC
        """)

        if categories:
            lines.append("\nBy Category:")
            for c in categories:
                pct = (c["count"] / total * 100) if total > 0 else 0
                lines.append(f"  {c['category']:25s} {c['count']:>6,}  ({pct:5.1f}%)")

        # Data source count
        sources = db.execute_query("""
            SELECT COUNT(DISTINCT name) as source_count
            FROM integrations
            WHERE status = 'active'
        """)
        if sources:
            lines.append(f"\nActive Data Sources: {sources[0]['source_count']}")

        # Stale assets (not seen in 30 days)
        stale = db.execute_query("""
            SELECT COUNT(*) as stale_count
            FROM assets
            WHERE last_seen < NOW() - INTERVAL '30 days'
        """)
        if stale and stale[0]["stale_count"]:
            lines.append(f"Stale Assets (>30 days): {stale[0]['stale_count']:,}")

        return "\n".join(lines)

    @mcp.resource("assetatlas://stats/data-sources")
    def stats_data_sources() -> str:
        """Data source health: asset counts, last sync, and coverage."""
        app_ctx = get_app_context()
        if not app_ctx:
            return "Database not available."

        db = app_ctx.db
        sources = db.execute_query("""
            SELECT
                i.name,
                i.integration_type,
                i.status,
                i.last_sync_at,
                COUNT(DISTINCT ap.asset_id) as asset_count,
                MAX(ap.last_seen) as last_data
            FROM integrations i
            LEFT JOIN asset_providers ap ON ap.data_source = i.name
            GROUP BY i.id, i.name, i.integration_type, i.status, i.last_sync_at
            ORDER BY asset_count DESC
        """)

        if not sources:
            return "No data sources configured."

        total_assets = db.execute_query("SELECT COUNT(*) as total FROM assets")
        total = total_assets[0]["total"] if total_assets else 0

        lines = ["Data Source Health\n"]
        lines.append(f"{'Source':25s} {'Type':15s} {'Status':8s} {'Assets':>8s} {'Coverage':>8s} Last Sync")
        lines.append("-" * 95)

        for s in sources:
            coverage = f"{s['asset_count'] / total * 100:.1f}%" if total > 0 else "0.0%"
            sync = str(s.get("last_sync_at") or "never")[:19]
            lines.append(
                f"{s['name']:25s} {s['integration_type']:15s} {s['status']:8s} "
                f"{s['asset_count']:>8,} {coverage:>8s} {sync}"
            )

        lines.append(f"\nTotal unique assets: {total:,}")

        return "\n".join(lines)
