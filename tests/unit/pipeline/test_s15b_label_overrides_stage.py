# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for AssetLabelOverridesStage (s15b)."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.modules.asset_generation.pipeline.context import PipelineContext
from src.modules.asset_generation.pipeline.stages.s15b_label_overrides import (
    AssetLabelOverridesStage,
)


def _ctx_with(df: pd.DataFrame) -> PipelineContext:
    ctx = PipelineContext(raw_data_df=pd.DataFrame())
    ctx.scored_df = df
    ctx.stats = {"stages": {}}
    return ctx


class TestAssetLabelOverridesStage:
    def test_skipped_when_disabled(self):
        service = SimpleNamespace(
            enable_asset_label_overrides=False,
            asset_label_override_repo=None,
        )
        stage = AssetLabelOverridesStage(service)
        ctx = _ctx_with(pd.DataFrame({"id": [uuid.uuid4()]}))
        stage._execute(ctx)
        assert ctx.stats["stages"]["asset_label_overrides"] == {"skipped": True}

    def test_applies_when_enabled(self):
        repo = MagicMock()
        repo.get_all_overrides.return_value = {"abc": "Lab Printer"}
        service = SimpleNamespace(
            enable_asset_label_overrides=True,
            asset_label_override_repo=repo,
        )
        df = pd.DataFrame({"id": ["abc", "def"]})
        repo.apply_overrides.return_value = df.assign(asset_label_override=["Lab Printer", None])

        stage = AssetLabelOverridesStage(service)
        ctx = _ctx_with(df)
        stage._execute(ctx)

        repo.get_all_overrides.assert_called_once()
        repo.apply_overrides.assert_called_once()
        stats = ctx.stats["stages"]["asset_label_overrides"]
        assert "skipped" not in stats
        assert stats["overrides_applied"] == 1
        assert "duration" in stats

    def test_no_repo_skips(self):
        service = SimpleNamespace(
            enable_asset_label_overrides=True,
            asset_label_override_repo=None,
        )
        stage = AssetLabelOverridesStage(service)
        ctx = _ctx_with(pd.DataFrame({"id": [uuid.uuid4()]}))
        stage._execute(ctx)
        assert ctx.stats["stages"]["asset_label_overrides"] == {"skipped": True}
