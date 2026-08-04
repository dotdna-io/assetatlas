# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ProviderPreparationStage (s11) — issue #681 A3.

The stage must NOT swallow its own exception. Provider tracking is only enabled
when the operator turns it on (should_skip returns True otherwise), so a failure
inside _execute is a real error that must abort the run — post-A1 the orchestrator
rolls the whole atomic transaction back. Swallowing it produced a run that looked
healthy but silently shipped empty asset_providers and skipped provider-conflict
detection.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.modules.asset_generation.pipeline.context import PipelineContext
from src.modules.asset_generation.pipeline.stages.s11_provider_preparation import (
    ProviderPreparationStage,
)


def _ctx_with(enriched_df: pd.DataFrame) -> PipelineContext:
    ctx = PipelineContext(raw_data_df=pd.DataFrame())
    ctx.enriched_df = enriched_df
    ctx.stats = {"stages": {}}
    return ctx


class TestProviderPreparationStage:
    def test_execute_propagates_preprocess_failure(self):
        """A failure in preprocess_for_providers must propagate, not be swallowed."""
        repo = MagicMock()
        repo.preprocess_for_providers.side_effect = RuntimeError("boom")
        service = SimpleNamespace(enable_provider_tracking=True, provider_repo=repo)

        stage = ProviderPreparationStage(service)
        ctx = _ctx_with(pd.DataFrame({"asset_id": ["a", "b"]}))

        with pytest.raises(RuntimeError, match="boom"):
            stage.execute(ctx)

    def test_execute_records_providers_prepared_on_success(self):
        """Happy path is unchanged: prepared frame stored, count recorded."""
        prepared = pd.DataFrame({"asset_id": ["a", "b", "c"]})
        repo = MagicMock()
        repo.preprocess_for_providers.return_value = prepared
        service = SimpleNamespace(enable_provider_tracking=True, provider_repo=repo)

        stage = ProviderPreparationStage(service)
        ctx = _ctx_with(pd.DataFrame({"asset_id": ["a", "b", "c"]}))

        stage.execute(ctx)

        assert ctx.providers_df_prepared is prepared
        stats = ctx.stats["stages"]["provider_prepare"]
        assert stats["providers_prepared"] == 3
        assert "error" not in stats
