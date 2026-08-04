# SPDX-License-Identifier: AGPL-3.0-or-later
"""Docs resources for the AssetAtlas MCP server.

Resources: query-guide
"""

from src.mcp.resources.schema import STANDARD_FIELDS


def register_resources(mcp):
    """Register docs resources on the FastMCP server."""

    @mcp.resource("assetatlas://docs/query-guide")
    def docs_query_guide() -> str:
        """How to construct asset queries using filters, search, and aggregations."""
        lines = [
            "AssetAtlas Query Guide",
            "=" * 40,
            "",
            "1. SEARCH (free-text)",
            "   Use search_assets(query) for quick lookups by hostname, IP, MAC, or serial.",
            "   Example: search_assets(query='web-server-01')",
            "",
            "2. FILTERED LISTING",
            "   Use list_assets(filters=...) for structured queries.",
            "   Filters is a JSON array of {field, op, value} objects.",
            "",
            "   Single filter:",
            '   list_assets(filters=\'[{"field":"category","op":"eq","value":"Server"}]\')',
            "",
            "   Multiple filters (AND logic):",
            '   list_assets(filters=\'[{"field":"category","op":"eq","value":"Server"},',
            '     {"field":"os_eol_status","op":"eq","value":"end_of_life"}]\')',
            "",
            "   Common operators:",
            "     eq       — exact match",
            "     neq      — not equal",
            "     contains — substring match (case-insensitive)",
            "     starts   — prefix match",
            "     ends     — suffix match",
            "     gt/gte   — greater than / greater or equal",
            "     lt/lte   — less than / less or equal",
            "     empty    — field is null/empty (no value needed)",
            "     notempty — field has a value (no value needed)",
            "     any      — matches any in comma-separated list",
            "",
            "3. AVAILABLE FIELDS",
        ]

        for key, dtype, desc in STANDARD_FIELDS:
            lines.append(f"     {key:25s} ({dtype})")

        lines.extend([
            "",
            "   Extended attributes use prefix notation: <source>__<field>",
            "   Example: intune_full__deviceEnrollmentType_s",
            "",
            "4. AGGREGATION",
            "   Use aggregate_assets(row_field, col_field) for counts and breakdowns.",
            "",
            "   1D: aggregate_assets(row_field='category')",
            "       → count of assets per category",
            "",
            "   2D: aggregate_assets(row_field='category', col_field='os_eol_status')",
            "       → pivot table of categories × EOL status",
            "",
            "5. FIELD VALUE DISCOVERY",
            "   Use get_field_values(field) to see what values exist.",
            "   Example: get_field_values(field='os_normalized')",
            "   Use search= to filter: get_field_values(field='manufacturer', search='Dell')",
            "",
            "6. PAGINATION & SORTING",
            "   list_assets(page=1, limit=50, sort_by='last_seen', sort_dir='desc')",
            "   Use fields= to limit columns: fields='hostname,ip,category,confidence'",
            "",
            "7. TIPS",
            "   - Start with search_assets for quick lookups",
            "   - Use get_field_values to discover valid filter values",
            "   - Use aggregate_assets for reporting and analysis",
            "   - Combine multiple filters for precise queries",
            "   - Check assetatlas://schema/operators for all operators per type",
        ])

        return "\n".join(lines)
