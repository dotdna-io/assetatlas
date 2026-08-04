# SPDX-License-Identifier: AGPL-3.0-or-later
"""s19 soft-lifecycle reconciliation is fully removed (#795)."""

import importlib

import pytest

from src.modules.asset_generation.services.asset_generation_service import (
    AssetGenerationService,
)


def test_s19_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            'src.modules.asset_generation.pipeline.stages.s19_lifecycle_reconciliation'
        )


def test_reconcile_method_removed():
    assert not hasattr(AssetGenerationService, '_reconcile_asset_lifecycle')
