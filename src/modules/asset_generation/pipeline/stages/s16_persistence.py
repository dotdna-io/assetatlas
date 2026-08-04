# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 9 — save assets & providers to database."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class PersistenceStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Saving Assets & Providers"

    @property
    def key(self) -> str:
        return "persistence"

    @property
    def stage_label(self) -> str:
        return "Saving Assets"

    def _execute(self, ctx: PipelineContext) -> None:
        # Join excluded records background write before persistence
        if ctx.excluded_records_future is not None:
            try:
                bg_saved = ctx.excluded_records_future.result(timeout=60)
                logger.info(
                    "  Background excluded records write completed: %d records",
                    bg_saved,
                )
            except Exception as e:
                logger.error(
                    "  Background excluded records write failed: %s",
                    e, exc_info=True,
                )
            finally:
                ctx.excluded_records_future = None

        persistence_result = self.service._persist_with_scope(
            ctx.scored_df, ctx.generation_run_id, ctx.providers_df_prepared,
        )

        ctx.stats['stages'][self.key] = {**persistence_result}
