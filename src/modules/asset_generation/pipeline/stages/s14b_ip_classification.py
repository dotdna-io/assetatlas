# SPDX-License-Identifier: AGPL-3.0-or-later
"""IP classification enrichment (post-aggregation, pre-scoring)."""

import logging
from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class IpClassificationStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "IP Classification"

    @property
    def key(self) -> str:
        return "ip_classification"

    @property
    def stage_label(self) -> str:
        return "IP Classification"

    def _execute(self, ctx: PipelineContext) -> None:
        # Runs BEFORE ScoringStage, so data is in ctx.aggregated_df
        target_df = ctx.aggregated_df
        if "ip" not in target_df.columns:
            logger.warning("  No ip column found — skipping IP classification")
            ctx.stats["stages"][self.key] = {"skipped": True, "reason": "no_ip_column"}
            return

        # 1. Classify IPs
        target_df = self.service.enricher.enrich_ip_classification(target_df)

        # 2. Collect stats from ip_type column before it's consumed
        type_counts = target_df["ip_type"].value_counts()
        ctx.stats["stages"][self.key] = {
            "private_only": int(type_counts.get("private_only", 0)),
            "public_only": int(type_counts.get("public_only", 0)),
            "mixed": int(type_counts.get("mixed", 0)),
            "none": int(type_counts.get("none", 0)),
        }

        # 3. Inject into extended_fields (renamed to extended_attributes by ScoringStage later)
        ef_col = "extended_fields"

        def _inject_assetatlas(row):
            ef = row.get(ef_col)
            if not isinstance(ef, dict):
                ef = {}
            else:
                ef = ef.copy()
            ef["AssetAtlas"] = {
                "ip_private": row.get("ip_private", []),
                "ip_public": row.get("ip_public", []),
                "ip_type": row.get("ip_type", "none"),
            }
            return ef

        target_df[ef_col] = target_df.apply(_inject_assetatlas, axis=1)

        # 4. Drop temp columns (data now lives in extended_fields)
        target_df = target_df.drop(columns=["ip_private", "ip_public", "ip_type"])

        # 5. Write back
        ctx.aggregated_df = target_df
