# SPDX-License-Identifier: AGPL-3.0-or-later
"""Workflow prompts for the AssetAtlas MCP server.

Prompts: investigate_asset, compliance_report, data_quality_audit,
         source_health_check, asset_inventory_summary
"""


def register_prompts(mcp):
    """Register workflow prompts on the FastMCP server."""

    @mcp.prompt()
    def investigate_asset(identifier: str) -> list[dict]:
        """Investigate an asset by hostname, IP, or MAC address.

        Gathers complete context: identity, providers, history, and anomalies.

        Args:
            identifier: Hostname, IP address, or MAC address to investigate
        """
        return [
            {
                "role": "user",
                "content": (
                    f"Investigate the asset identified by '{identifier}'. "
                    "Follow these steps:\n\n"
                    "1. **Search**: Call search_assets with the identifier to find matching assets.\n"
                    "2. **Detail**: For the best match, call get_asset with its UUID to get "
                    "full details including all providers and extended attributes.\n"
                    "3. **History**: Call get_asset_history with the asset UUID to see recent "
                    "changes and events.\n"
                    "4. **Analysis**: Review the gathered data and report:\n"
                    "   - Asset identity (hostname, IPs, MACs, serial numbers)\n"
                    "   - Classification (category, OS, manufacturer/model)\n"
                    "   - Data source coverage — which integrations report this asset?\n"
                    "   - Confidence score and any conflicting fields between sources\n"
                    "   - Recent changes or notable events from history\n"
                    "   - Any anomalies: missing expected fields, stale data, low confidence\n\n"
                    "Present findings in a structured summary with sections for each area."
                ),
            }
        ]

    @mcp.prompt()
    def compliance_report(
        category: str = "",
        scope: str = "all",
    ) -> list[dict]:
        """Analyze compliance posture across the asset inventory.

        Args:
            category: Optional asset category to focus on (e.g. 'Server', 'Desktop')
            scope: Analysis scope — 'all', 'tagged', or 'untagged' (default: all)
        """
        category_filter = ""
        if category:
            category_filter = (
                f"\nFocus on category '{category}' by adding a filter: "
                f'[{{"field":"category","op":"eq","value":"{category}"}}]'
            )

        scope_instruction = ""
        if scope == "tagged":
            scope_instruction = "\nFocus only on assets that have compliance-related tags."
        elif scope == "untagged":
            scope_instruction = "\nFocus on assets that are missing expected compliance tags."

        return [
            {
                "role": "user",
                "content": (
                    "Generate a compliance report for the asset inventory. "
                    "Follow these steps:\n\n"
                    "1. **Tag Rules**: Call list_tag_rules to understand what compliance "
                    "rules are configured and their current status.\n"
                    "2. **Aggregate by Tags**: Use aggregate_assets with row_field='category' "
                    "to see the overall distribution."
                    f"{category_filter}\n"
                    "3. **OS EOL Analysis**: Use aggregate_assets with "
                    "row_field='category' and col_field='os_eol_status' to identify "
                    "assets running end-of-life operating systems.\n"
                    "4. **Gap Analysis**: Identify categories or groups that lack "
                    "expected compliance tags or have low tag coverage.\n"
                    "5. **Summary**: Present findings as a compliance report with:\n"
                    "   - Overall compliance posture (good/needs attention/critical)\n"
                    "   - Tag rule coverage and effectiveness\n"
                    "   - OS end-of-life exposure by category\n"
                    "   - Specific gaps or risks identified\n"
                    "   - Recommended actions prioritized by risk"
                    f"{scope_instruction}"
                ),
            }
        ]

    @mcp.prompt()
    def data_quality_audit(category: str = "") -> list[dict]:
        """Assess data quality across the asset inventory.

        Args:
            category: Optional category to focus on (e.g. 'Server', 'Desktop')
        """
        category_note = ""
        if category:
            category_note = f" Focus on category '{category}'."

        return [
            {
                "role": "user",
                "content": (
                    f"Perform a data quality audit of the asset inventory.{category_note} "
                    "Follow these steps:\n\n"
                    f"1. **Quality Metrics**: Call get_data_quality"
                    f"{'(category=\"' + category + '\")' if category else '()'} "
                    "to get confidence distribution and field coverage.\n"
                    "2. **Confidence Breakdown**: Use aggregate_assets with "
                    "row_field='category' to see asset counts per category, then "
                    "cross-reference with confidence data.\n"
                    "3. **Source Coverage**: Call get_field_values(field='data_source') "
                    "to see which sources contribute data and their relative coverage.\n"
                    "4. **Low-Confidence Assets**: Use list_assets with a filter for "
                    'confidence < 0.5: [{\"field\":\"confidence\",\"op\":\"lt\",\"value\":\"0.5\"}] '
                    "to sample problematic assets.\n"
                    "5. **Report**: Present a data quality report with:\n"
                    "   - Overall quality score (based on avg confidence)\n"
                    "   - Confidence distribution (high/medium/low percentages)\n"
                    "   - Field coverage gaps — which important fields are often empty?\n"
                    "   - Source overlap — assets seen by only one source vs multiple\n"
                    "   - Specific quality issues found with examples\n"
                    "   - Recommendations to improve data quality"
                ),
            }
        ]

    @mcp.prompt()
    def source_health_check() -> list[dict]:
        """Assess integration health and data freshness across all sources."""
        return [
            {
                "role": "user",
                "content": (
                    "Perform a health check on all configured data source integrations. "
                    "Follow these steps:\n\n"
                    "1. **Integration Status**: Read the resource at "
                    "assetatlas://config/integrations to see all configured sources.\n"
                    "2. **Detailed Check**: Call get_integration_status for each "
                    "active integration to get asset counts and sync details.\n"
                    "3. **Pipeline Status**: Call get_pipeline_status(include_history=True) "
                    "to check if the asset generation pipeline is running and recent "
                    "run results.\n"
                    "4. **Source Coverage**: Read assetatlas://stats/data-sources "
                    "for coverage percentages.\n"
                    "5. **Report**: Present a health check report with:\n"
                    "   - Status of each integration (healthy/warning/error)\n"
                    "   - Last successful sync time for each source\n"
                    "   - Any sources that haven't synced recently (>24h)\n"
                    "   - Asset coverage per source and overlap analysis\n"
                    "   - Pipeline execution status and recent run results\n"
                    "   - Recommended actions for any issues found"
                ),
            }
        ]

    @mcp.prompt()
    def asset_inventory_summary(format: str = "brief") -> list[dict]:
        """Executive summary of the IT asset inventory state.

        Args:
            format: Output format — 'brief' for key metrics, 'detailed' for full analysis
        """
        if format == "detailed":
            detail_instruction = (
                "Provide a **detailed** analysis. Include specific numbers, "
                "breakdowns by category and subcategory, source-by-source analysis, "
                "and trend observations from recent pipeline runs."
            )
        else:
            detail_instruction = (
                "Provide a **brief** executive summary. Focus on key metrics, "
                "notable issues, and top-priority action items. Keep it concise."
            )

        return [
            {
                "role": "user",
                "content": (
                    f"Generate an IT asset inventory summary. {detail_instruction}\n\n"
                    "Follow these steps:\n\n"
                    "1. **Overview**: Read assetatlas://stats/summary for total counts "
                    "and quality metrics.\n"
                    "2. **Categories**: Use aggregate_assets(row_field='category') for "
                    "the category breakdown.\n"
                    "3. **Quality**: Call get_data_quality() for confidence and coverage "
                    "metrics.\n"
                    "4. **Sources**: Read assetatlas://stats/data-sources for source "
                    "health overview.\n"
                    "5. **Alerts**: Call get_alerts(limit=5) to check for any active "
                    "alerts or recent issues.\n"
                    "6. **Summary**: Present the inventory summary with:\n"
                    "   - Total asset count and confidence overview\n"
                    "   - Category distribution (top categories with counts)\n"
                    "   - Data quality highlights (coverage, confidence)\n"
                    "   - Source health status\n"
                    "   - Active alerts or issues\n"
                    "   - Key findings and recommended actions"
                ),
            }
        ]
