# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 1 — preprocess raw ingested data."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class PreprocessingStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 1/12: Preprocessing"

    @property
    def key(self) -> str:
        return "preprocessing"

    @property
    def stage_label(self) -> str:
        return "Preprocessing"

    def _execute(self, ctx: PipelineContext) -> None:
        ctx.preprocessed_df = self.service._preprocess_data(ctx.raw_data_df)

        logger.info(
            "  Preprocessed %d rows", len(ctx.preprocessed_df),
        )
