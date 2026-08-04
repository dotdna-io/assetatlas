# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for PipelineStage.execute() last_stage attribution — #681 latent bug.

When a stage raises, ctx.stats['last_stage'] must name the FAILING stage (the
orchestrator's failure log reads it), not the previously-completed stage.
"""

import pandas as pd
import pytest

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext


class _PassStage(PipelineStage):
    @property
    def name(self) -> str:
        return "Pass"

    @property
    def key(self) -> str:
        return "pass_stage"

    def _execute(self, ctx: PipelineContext) -> None:
        pass


class _BoomStage(PipelineStage):
    @property
    def name(self) -> str:
        return "Boom"

    @property
    def key(self) -> str:
        return "boom_stage"

    def _execute(self, ctx: PipelineContext) -> None:
        raise RuntimeError("kaboom")


class TestLastStageAttribution:
    def test_failing_stage_is_recorded_not_the_previous_one(self):
        ctx = PipelineContext(raw_data_df=pd.DataFrame())

        # A prior stage completes and claims last_stage.
        _PassStage().execute(ctx)
        assert ctx.stats["last_stage"] == "pass_stage"

        # The next stage raises mid-_execute.
        with pytest.raises(RuntimeError, match="kaboom"):
            _BoomStage().execute(ctx)

        # last_stage must point at the FAILING stage, not the completed one.
        # Pre-fix this is still "pass_stage" (set only after _execute) -> FAILS.
        assert ctx.stats["last_stage"] == "boom_stage"

    def test_successful_stage_still_records_last_stage(self):
        # Happy path unchanged: a stage that completes claims last_stage.
        ctx = PipelineContext(raw_data_df=pd.DataFrame())
        _PassStage().execute(ctx)
        assert ctx.stats["last_stage"] == "pass_stage"
