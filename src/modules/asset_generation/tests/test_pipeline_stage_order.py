# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grace stage runs just before persistence; s19 is gone (#795).

Inspects the orchestrator source (no DB / no instantiation) for the stage
construction order — robust to the constructor's DB/enrichment wiring.
"""

import inspect

from src.modules.asset_generation.services import pipeline_orchestrator


def _src():
    return inspect.getsource(pipeline_orchestrator.PipelineOrchestrator.__init__)


def test_grace_stage_precedes_persistence():
    src = _src()
    assert 'GraceCarryForwardStage(s)' in src
    assert src.index('GraceCarryForwardStage(s)') < src.index('PersistenceStage(s)')


def test_lifecycle_reconciliation_removed():
    # No longer constructed (its import is also removed).
    assert 'LifecycleReconciliationStage(' not in _src()
