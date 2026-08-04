# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 10 — change tracking (chronicle)."""

import logging

import pandas as pd

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class ChangeTrackingStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Tracking Changes (Chronicle)"

    @property
    def key(self) -> str:
        return "chronicle"

    @property
    def stage_label(self) -> str:
        return "Change Tracking"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return not ctx.track_changes

    def _load_current_tags(self, asset_ids: list) -> pd.DataFrame:
        """Load current tags from DB for given asset IDs (populated by Stage 9.5 tag rules)."""
        if not asset_ids:
            return pd.DataFrame(columns=['id', 'tags'])
        try:
            rows = self.service.db.execute_query(
                "SELECT id::text, tags FROM assets WHERE id = ANY(%(ids)s::uuid[]) AND tags IS NOT NULL",
                {'ids': asset_ids}
            )
            if rows:
                return pd.DataFrame(rows, columns=['id', 'tags'])
        except Exception as e:
            logger.warning("Could not load tags for chronicle: %s", e)
        return pd.DataFrame(columns=['id', 'tags'])

    def _load_current_posture_rules(self, asset_ids: list) -> pd.DataFrame:
        """Load current posture_rules from DB for given asset IDs (populated by Stage 9.6 posture rules)."""
        if not asset_ids:
            return pd.DataFrame(columns=['id', 'posture_rules'])
        try:
            rows = self.service.db.execute_query(
                "SELECT id::text, posture_rules FROM assets WHERE id = ANY(%(ids)s::uuid[])",
                {'ids': asset_ids}
            )
            if rows:
                return pd.DataFrame(rows, columns=['id', 'posture_rules'])
        except Exception as e:
            logger.warning("Could not load posture_rules for chronicle: %s", e)
        return pd.DataFrame(columns=['id', 'posture_rules'])

    def _load_current_extended_attributes(self, asset_ids: list) -> pd.DataFrame:
        """Load current extended_attributes from DB for given asset IDs.

        CsvEnrichmentStage merges the 'enrichment' sub-dict back into
        extended_attributes via SQL — that branch is invisible to scored_df,
        so we must re-load to see it during chronicle comparison.
        """
        if not asset_ids:
            return pd.DataFrame(columns=['id', 'extended_attributes'])
        try:
            rows = self.service.db.execute_query(
                "SELECT id::text, extended_attributes FROM assets WHERE id = ANY(%(ids)s::uuid[])",
                {'ids': asset_ids}
            )
            if rows:
                return pd.DataFrame(rows, columns=['id', 'extended_attributes'])
        except Exception as e:
            logger.warning("Could not load extended_attributes for chronicle: %s", e)
        return pd.DataFrame(columns=['id', 'extended_attributes'])

    def _execute(self, ctx: PipelineContext) -> None:
        # Guard: chronicle prerequisites must be loaded before persistence (s14 before s16)
        if ctx.old_assets_df is None:
            raise RuntimeError(
                "old_assets_df is None — chronicle requires the pre-save snapshot. "
                "Ensure s14 (scoring) runs before s16 (persistence) when track_changes=True."
            )
        if ctx.historical_ids is None:
            raise RuntimeError(
                "historical_ids is None — chronicle requires historical IDs for re-add detection. "
                "Ensure s14 (scoring) loads historical IDs when track_changes=True."
            )

        # Inject DB-side state into scored_df so chronicle can detect real changes.
        # tags (s17), posture_rules (s17b), and the enrichment sub-dict of
        # extended_attributes (s21 CSV enrichment) are all written directly to
        # the DB by stages running after persistence — none of them flow back
        # into scored_df. Without this re-load, chronicle sees them going from
        # populated → empty on every run and fires a spurious update per asset.
        asset_ids = ctx.scored_df['id'].tolist()

        tags_df = self._load_current_tags(asset_ids)
        if not tags_df.empty:
            if 'tags' in ctx.scored_df.columns:
                ctx.scored_df = ctx.scored_df.drop(columns=['tags'])
            ctx.scored_df = ctx.scored_df.merge(tags_df, on='id', how='left')

        posture_df = self._load_current_posture_rules(asset_ids)
        if not posture_df.empty:
            if 'posture_rules' in ctx.scored_df.columns:
                ctx.scored_df = ctx.scored_df.drop(columns=['posture_rules'])
            ctx.scored_df = ctx.scored_df.merge(posture_df, on='id', how='left')

        ext_attrs_df = self._load_current_extended_attributes(asset_ids)
        if not ext_attrs_df.empty:
            if 'extended_attributes' in ctx.scored_df.columns:
                ctx.scored_df = ctx.scored_df.drop(columns=['extended_attributes'])
            ctx.scored_df = ctx.scored_df.merge(ext_attrs_df, on='id', how='left')

        chronicle_stats = self.service._track_changes(
            ctx.old_assets_df,
            ctx.scored_df,
            ctx.generation_run_id,
            providers_df=ctx.providers_df_prepared,
            historical_ids=ctx.historical_ids,
            merge_pairs=ctx.merge_pairs,
        )
        ctx.stats['stages'][self.key] = {**chronicle_stats}

        # Surface chronicle counts as top-level stats for consumers
        event_counts = chronicle_stats.get('event_type_counts', {})
        ctx.stats['new_assets'] = (
            event_counts.get('asset_created', 0)
            + event_counts.get('asset_readded', 0)
        )
        ctx.stats['updated_assets'] = event_counts.get('attribute_updated', 0)
        # Split identity consolidations out of the removed count: "removed"
        # reports genuine departures, "merged" reports pipeline_merge absorptions.
        # merged is a subset of asset_removed (both counted from the same events),
        # so the difference is always >= 0.
        merged = chronicle_stats.get('merged_assets', 0)
        ctx.stats['merged_assets'] = merged
        ctx.stats['removed_assets'] = event_counts.get('asset_removed', 0) - merged
