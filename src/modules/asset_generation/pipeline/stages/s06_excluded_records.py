# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 4.6-4.7 — prepare/write excluded records and filter them out."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class ExcludedRecordsStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 4.6-4.7/12: Excluded Records"

    @property
    def key(self) -> str:
        return "excluded_records"

    @property
    def stage_label(self) -> str:
        return "Excluded Records"

    def _execute(self, ctx: PipelineContext) -> None:
        # Stage 4.6: Prepare and submit background write
        excluded_records, excluded_count = self.service._prepare_excluded_records(
            ctx.id_assigned_df, ctx.generation_run_id
        )

        if excluded_records:
            ctx.excluded_records_future = self.service._executor.submit(
                self.service._write_excluded_records_background,
                excluded_records,
                ctx.generation_run_id,
                excluded_count,
            )
            logger.info(
                "  Submitted %d excluded records for background write",
                excluded_count,
            )
        else:
            ctx.excluded_records_future = None
            logger.info("  No excluded records to save")

        ctx.excluded_count = excluded_count
        ctx.stats['stages'][self.key] = {
            'excluded_count': excluded_count,
        }
        ctx.stats['excluded_records'] = excluded_count

        # Stage 4.7: Filter out excluded records
        before_count = len(ctx.id_assigned_df)
        ctx.valid_records_df = ctx.id_assigned_df[
            ctx.id_assigned_df['id'] != '<NA>'
        ].copy()
        filtered_count = before_count - len(ctx.valid_records_df)

        logger.info(
            "  Filtered out %d excluded records from pipeline "
            "(%d valid records continue)",
            filtered_count, len(ctx.valid_records_df),
        )
        if filtered_count != excluded_count:
            logger.warning(
                "  Mismatch: Saved %d excluded records, "
                "but filtered %d from pipeline",
                excluded_count, filtered_count,
            )

        # Per-source stats: unique assets and excluded records per batch_id.
        # Capture the integration name (the raw df's `data_source` = i.name) so the
        # anomaly engine (s20) can key per-source volume baselines on a stable name
        # rather than the per-run batch_id (see AnomalyDetectionService A3).
        if 'batch_id' in ctx.id_assigned_df.columns:
            has_source = 'data_source' in ctx.id_assigned_df.columns
            per_source = {}
            for bid, grp in ctx.id_assigned_df.groupby('batch_id'):
                excluded_in_source = (grp['id'] == '<NA>').sum()
                unique_in_source = grp.loc[grp['id'] != '<NA>', 'id'].nunique()
                entry = {
                    'unique_assets': int(unique_in_source),
                    'excluded': int(excluded_in_source),
                }
                if has_source:
                    names = grp['data_source'].dropna()
                    if not names.empty:
                        entry['integration_name'] = str(names.iloc[0])
                per_source[str(bid)] = entry
            ctx.stats['per_source'] = per_source

        # Resource limit check: unique assets
        unique_count = ctx.valid_records_df['id'].nunique()
        if unique_count > self.service.MAX_UNIQUE_ASSETS:
            raise ValueError(
                f"Pipeline produced {unique_count} unique assets, "
                f"exceeding limit of {self.service.MAX_UNIQUE_ASSETS}. "
                f"Check input data for anomalies."
            )
