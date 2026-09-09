# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pipeline completion bumps the asset-data version stamp (#1410).

A run with work TRUNCATEs + rebuilds `assets`, so every cached Explorer
field-values entry is stale the moment the run lands. The bump lives in
`_execute_run`'s `finally` next to the field-catalog stamp bump (the one
point every terminal path reaches), so the cache keys change fleet-wide
without a pattern delete. It must fire on EVERY terminal path — success,
failed-result, and exception — because a run that died mid-rebuild may
already have committed stages.

Harness mirrors test_pipeline_catalog_stamp_bump.py: a mock adapter fed
statements in order, the trigger service stubbed.
"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from src.modules.pipeline.pipeline_service import PipelineService

RUN_ID = UUID("11111111-2222-3333-4444-555555555555")

_BUMP = "src.modules.pipeline.pipeline_service.asset_data_version.bump"


def _service(result=None, raises=None):
    svc = PipelineService.__new__(PipelineService)
    svc.connection_manager = MagicMock()
    svc.trigger_service = MagicMock()
    gen = svc.trigger_service.generate_assets_for_latest_batches
    if raises is not None:
        gen.side_effect = raises
    else:
        gen.return_value = result
    # Dashboard-cache invalidation is unrelated to the stamp; stubbed.
    svc._invalidate_dashboard_cache = MagicMock()
    return svc


def _execute(svc, db_returns):
    """Run _execute_run against a mock adapter fed statements in order."""
    db = MagicMock()
    db.execute_query.side_effect = list(db_returns)
    with patch("src.modules.pipeline.pipeline_service.PostgreSQLAdapter",
               return_value=db):
        svc._execute_run(RUN_ID)
    return db


class TestPipelineCompletionBumpsAssetDataStamp:
    def test_successful_run_bumps(self):
        svc = _service(result={"success": True, "stats": {}})
        # [mark-running, terminal-update RETURNING, settle-stamp]
        with patch(_BUMP) as mock_bump:
            _execute(svc, [None, [{"id": RUN_ID}], None])
        mock_bump.assert_called_once_with()

    def test_failed_result_still_bumps(self):
        """A run whose result reports failure may have committed stages."""
        svc = _service(result={"success": False, "error": "stage blew up"})
        with patch(_BUMP) as mock_bump:
            _execute(svc, [None, [{"id": RUN_ID}], None])
        mock_bump.assert_called_once_with()

    def test_exception_still_bumps(self):
        """The finally covers the raising path too — same reasoning as the
        catalog bump it sits next to."""
        svc = _service(raises=RuntimeError("orchestrator died"))
        with patch(_BUMP) as mock_bump:
            _execute(svc, [None, [{"id": RUN_ID}], None])
        mock_bump.assert_called_once_with()
