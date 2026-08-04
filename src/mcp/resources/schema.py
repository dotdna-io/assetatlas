# SPDX-License-Identifier: AGPL-3.0-or-later
"""Schema resources for the AssetAtlas MCP server.

Resources: fields, categories, operators
"""

import logging

from src.mcp.context import get_app_context

logger = logging.getLogger(__name__)

# Standard fields with their API keys, types, and descriptions
STANDARD_FIELDS = [
    ("hostname", "string_array", "Device hostnames (multi-valued from multiple sources)"),
    ("fqdn", "string_array", "Fully qualified domain names"),
    ("ip", "string_array", "IP addresses (IPv4/IPv6)"),
    ("mac", "string_array", "MAC addresses (normalized format)"),
    ("sn", "string_array", "Serial numbers"),
    ("category", "string", "Asset category (Desktop, Server, Network Device, etc.)"),
    ("subcategory", "string", "Asset subcategory (Laptop, Virtual Machine, Switch, etc.)"),
    ("os_normalized", "string", "Normalized operating system name"),
    ("mac_vendor_enriched", "string_array", "MAC address vendor (enriched via OUI lookup)"),
    ("manufacturer", "string_array", "Hardware manufacturer"),
    ("model", "string_array", "Hardware model"),
    ("data_source", "string_array", "Integration names that reported this asset"),
    ("owner", "string_array", "Asset owners (one per source that reported one)"),
    ("location", "string_array", "Physical/logical locations (one per source)"),
    ("user_field", "string_array", "Custom user-defined field values (one per source)"),
    ("confidence", "float", "Confidence score (0.0 to 1.0)"),
    ("os_eol_status", "string", "OS end-of-life status: active, approaching_eol, end_of_life"),
    ("os_eol_date", "date", "OS end-of-life date"),
    ("os_is_lts", "boolean", "Whether OS is a Long Term Support version"),
    ("first_seen", "timestamp", "When asset was first discovered"),
    ("last_seen", "timestamp", "When asset was last seen by any source"),
    ("ipam_location", "string_array", "Locations from IPAM matching (one per source)"),
    ("data_source_count", "integer", "Number of distinct data sources reporting this asset"),
    ("hostname_count",    "integer", "Number of distinct hostnames reported across sources"),
    ("ip_count",          "integer", "Number of distinct IPs reported across sources"),
    ("fqdn_count",        "integer", "Number of distinct FQDNs reported across sources"),
    ("user_count",        "integer", "Number of distinct users reported across sources"),
    ("owner_count",       "integer", "Number of distinct owners reported across sources"),
    ("asset_label",       "string",  "Primary display identifier — manual override OR first non-empty hostname/fqdn/mac/sn/ip"),
]

# Operator definitions per data type — maps to the 'op' parameter in filters
OPERATORS_BY_TYPE = {
    "string": [
        ("eq", "Equals (exact, case-insensitive)"),
        ("neq", "Not equals"),
        ("contains", "Contains substring (case-insensitive)"),
        ("ncontains", "Does not contain substring (case-insensitive)"),
        ("starts", "Starts with (case-insensitive)"),
        ("ends", "Ends with (case-insensitive)"),
        ("any", "Matches any value in the comma-separated list"),
        ("none", "Matches none of the values in the comma-separated list"),
        ("empty", "Field is empty/null"),
        ("notempty", "Field is not empty"),
    ],
    "string_array": [
        ("eq", "Array contains the exact value"),
        ("neq", "Array does not contain the value"),
        ("contains", "Any array element contains substring"),
        ("ncontains", "No array element contains substring"),
        ("starts", "Any array element starts with"),
        ("ends", "Any array element ends with"),
        ("any", "Array contains any of the listed values"),
        ("all", "Array contains all of the listed values"),
        ("none", "Array contains none of the listed values"),
        ("empty", "Array is empty/null"),
        ("notempty", "Array has at least one value"),
    ],
    "float": [
        ("eq", "Equals"), ("neq", "Not equals"),
        ("gt", "Greater than"), ("gte", "Greater than or equal"),
        ("lt", "Less than"), ("lte", "Less than or equal"),
        ("between", "Within an inclusive [lo, hi] range"),
        ("empty", "Is null"), ("notempty", "Is not null"),
    ],
    "integer": [
        ("eq", "Equals"), ("neq", "Not equals"),
        ("gt", "Greater than"), ("gte", "Greater than or equal"),
        ("lt", "Less than"), ("lte", "Less than or equal"),
        ("between", "Within an inclusive [lo, hi] range"),
        ("empty", "Is null"), ("notempty", "Is not null"),
    ],
    "date": [
        ("eq", "Equals"), ("neq", "Not equals"),
        ("gt", "After"), ("gte", "On or after"),
        ("lt", "Before"), ("lte", "On or before"),
        ("between", "Within an inclusive [lo, hi] date range"),
        ("within_last", 'Within the last N units (e.g. "30d", units s/m/h/d/w)'),
        ("empty", "Is null"), ("notempty", "Is not null"),
    ],
    "timestamp": [
        ("eq", "Equals"), ("neq", "Not equals"),
        ("gt", "After"), ("gte", "On or after"),
        ("lt", "Before"), ("lte", "On or before"),
        ("between", "Within an inclusive [lo, hi] date range"),
        ("within_last", 'Within the last N units (e.g. "30d", units s/m/h/d/w)'),
        ("empty", "Is null"), ("notempty", "Is not null"),
    ],
    "boolean": [
        ("eq", "Equals (true/false)"),
        ("empty", "Is null"), ("notempty", "Is not null"),
    ],
    "jsonb": [
        ("contains", "Contains substring (case-insensitive)"),
        ("ncontains", "Does not contain substring (case-insensitive)"),
        ("empty", "Is empty/null"), ("notempty", "Is not empty"),
    ],
    "jsonb_array": [
        ("eq", "Array contains the exact value"),
        ("neq", "Array does not contain the value"),
        ("contains", "Any array element contains substring"),
        ("ncontains", "No array element contains substring"),
        ("starts", "Any array element starts with"),
        ("ends", "Any array element ends with"),
        ("any", "Array contains any of the listed values"),
        ("all", "Array contains all of the listed values"),
        ("none", "Array contains none of the listed values"),
        ("empty", "Array is empty/null"),
        ("notempty", "Array has at least one value"),
    ],
}


def register_resources(mcp):
    """Register schema resources on the FastMCP server."""

    @mcp.resource("assetatlas://schema/fields")
    def schema_fields() -> str:
        """All available asset fields with types and descriptions.

        Includes standard fields (always present) and extended attributes
        (dynamically discovered from data source integrations).
        """
        lines = ["AssetAtlas Field Reference\n"]
        lines.append("Standard Fields:")
        lines.append(f"{'Field Key':25s} {'Type':15s} Description")
        lines.append("-" * 80)
        for key, dtype, desc in STANDARD_FIELDS:
            lines.append(f"{key:25s} {dtype:15s} {desc}")

        # Extended attributes from DB
        app_ctx = get_app_context()
        if app_ctx:
            try:
                db = app_ctx.db
                sources = db.execute_query("""
                    SELECT DISTINCT source.key as source_name
                    FROM assets,
                         LATERAL jsonb_each(extended_attributes) AS source(key, value)
                    WHERE extended_attributes IS NOT NULL
                    ORDER BY source_name
                """)
                if sources:
                    lines.append("\nExtended Attributes (from data sources):")
                    lines.append("  Access via filter field: <source>__<field_name>")
                    lines.append("  Example: intune_full__deviceEnrollmentType_s\n")
                    for s in sources:
                        name = s["source_name"]
                        prefix = name.lower().replace(" ", "_")
                        # Get sample fields for this source
                        sample = db.execute_query("""
                            SELECT DISTINCT field.key as field_name
                            FROM assets,
                                 LATERAL jsonb_each(extended_attributes->%(src)s) AS field(key, value)
                            WHERE extended_attributes->%(src)s IS NOT NULL
                            ORDER BY field_name
                            LIMIT 20
                        """, {"src": name})
                        field_names = [f["field_name"] for f in (sample or [])]
                        lines.append(f"  {name} (prefix: {prefix}__)")
                        if field_names:
                            lines.append(f"    Fields: {', '.join(field_names[:15])}")
                            if len(field_names) > 15:
                                lines.append("    ... and more")
            except Exception as e:
                logger.warning(f"Failed to load extended attributes: {e}")

        return "\n".join(lines)

    @mcp.resource("assetatlas://schema/categories")
    def schema_categories() -> str:
        """Category and subcategory taxonomy with current asset counts."""
        app_ctx = get_app_context()
        if not app_ctx:
            return "Database not available."

        db = app_ctx.db
        categories = db.execute_query("""
            SELECT
                COALESCE(category, 'Uncategorized') as category,
                COALESCE(subcategory, '(none)') as subcategory,
                COUNT(*) as count
            FROM assets
            GROUP BY category, subcategory
            ORDER BY category, count DESC
        """)

        if not categories:
            return "No categories found."

        lines = ["AssetAtlas Category Taxonomy\n"]
        current_cat = None
        for row in categories:
            cat = row["category"]
            if cat != current_cat:
                if current_cat is not None:
                    lines.append("")
                cat_total = sum(r["count"] for r in categories if r["category"] == cat)
                lines.append(f"{cat} ({cat_total:,} assets)")
                current_cat = cat
            lines.append(f"  {row['subcategory']:30s} {row['count']:>6,}")

        return "\n".join(lines)

    @mcp.resource("assetatlas://schema/operators")
    def schema_operators() -> str:
        """Filter operators available per field data type.

        Use these operators in the 'op' field of filter objects
        when calling list_assets or other filtered tools.
        """
        lines = ["AssetAtlas Filter Operators\n"]
        lines.append("Usage in filters: {\"field\": \"<field_key>\", \"op\": \"<operator>\", \"value\": \"<value>\"}\n")

        for dtype, ops in OPERATORS_BY_TYPE.items():
            lines.append(f"{dtype}:")
            for op_key, op_desc in ops:
                lines.append(f"  {op_key:12s} {op_desc}")
            lines.append("")

        lines.append("Notes:")
        lines.append("- 'empty' and 'notempty' operators do not require a 'value' field")
        lines.append("- 'any', 'all', and 'none' accept a comma-separated list as value "
                     "(any = matches at least one; all = contains every listed value; "
                     "none = contains no listed value)")
        lines.append("- 'between' takes an inclusive range as \"lo,hi\"; 'within_last' takes a "
                     "relative duration like \"30d\" (units s/m/h/d/w)")
        lines.append("- String operators are case-insensitive")
        lines.append("- Extended attribute fields use the same operators as their base type (usually string)")

        return "\n".join(lines)
