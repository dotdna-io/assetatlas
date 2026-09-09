# SPDX-License-Identifier: AGPL-3.0-or-later
"""refresh_tags_column bumps the asset-data version stamp post-commit (#1410).

A tag-rule execution (single-rule execute or execute-all) rewrites the
denormalised assets.tags map fleet-wide, so every tag: field's distinct
values change and the Explorer field-values cache must invalidate.

The bump's placement depends on the caller's transaction context:

  * request path (the execute routes): the UPDATE commits only at request
    teardown, so the bump runs inside outside_request_scope — end_scope (the
    commit) must precede the bump, or a concurrent /values request could read
    the new stamp against the pre-write snapshot and cache stale tag values
    under the new key;
  * pipeline thread (s17, inside the orchestrator's atomic write scope): the
    scope must NOT be unwound, so no bump — PipelineService's run-completion
    bump covers invalidation;
  * no active scope (CLI): statements auto-commit, so the bump fires
    directly;
  * a failed refresh bumps nothing.
"""

from unittest.mock import MagicMock, patch

from src.modules.tag_rules.services.tags_column_service import refresh_tags_column

_BUMP = "src.modules.tag_rules.services.tags_column_service.asset_data_version.bump"
_IN_REQUEST = "src.modules.tag_rules.services.tags_column_service.has_request_context"


def _db(scope_active=True):
    db = MagicMock()
    db.conn_manager.is_scope_active.return_value = scope_active
    return db


def test_request_path_bumps_after_the_scope_commits():
    db = _db()
    order = MagicMock()
    order.attach_mock(db.conn_manager.end_scope, "end_scope")
    with patch(_IN_REQUEST, return_value=True), patch(_BUMP) as mock_bump:
        order.attach_mock(mock_bump, "bump")
        assert refresh_tags_column(db) == {"status": "success"}
    names = [c[0] for c in order.mock_calls]
    assert names == ["end_scope", "bump"]


def test_pipeline_path_never_unwinds_the_write_scope_and_does_not_bump():
    """s17 runs inside the orchestrator's scope: no end_scope, no bump —
    the run-completion bump in PipelineService covers invalidation."""
    db = _db(scope_active=True)
    with patch(_IN_REQUEST, return_value=False), patch(_BUMP) as mock_bump:
        assert refresh_tags_column(db) == {"status": "success"}
    db.conn_manager.end_scope.assert_not_called()
    mock_bump.assert_not_called()


def test_scope_free_path_bumps_directly():
    db = _db(scope_active=False)
    with patch(_IN_REQUEST, return_value=False), patch(_BUMP) as mock_bump:
        assert refresh_tags_column(db) == {"status": "success"}
    mock_bump.assert_called_once_with()


def test_a_failed_refresh_bumps_nothing():
    db = _db()
    db.execute_query.side_effect = RuntimeError("boom")
    with patch(_IN_REQUEST, return_value=True), patch(_BUMP) as mock_bump:
        assert refresh_tags_column(db)["status"] == "failed"
    mock_bump.assert_not_called()
