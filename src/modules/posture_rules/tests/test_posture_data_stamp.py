# SPDX-License-Identifier: AGPL-3.0-or-later
"""Posture evaluation bumps the asset-data version stamp post-commit (#1410).

evaluate_all / evaluate_category write per-asset state into
assets.posture_rules, which the posture_state:/posture_output: fields read —
their distinct values change, so the Explorer field-values cache must
invalidate. The stamp must move only AFTER the write commits:

  * request path (execute_all route, sub-rule CUD re-eval): the write commits
    at request teardown, so the bump runs inside outside_request_scope —
    end_scope (the commit) must precede the bump, or a concurrent /values
    request could read the new stamp against the pre-write snapshot and cache
    stale posture values under the new key;
  * pipeline thread (s17b, inside the orchestrator's atomic write scope): the
    scope must NOT be unwound, so no end_scope and no bump — the
    run-completion bump in PipelineService covers invalidation;
  * a skipped evaluation (advisory lock held elsewhere) wrote nothing and
    bumps nothing.

The sub-rule toggle/delete path that clears the last-enabled rule's category
state (clear_category_state, no re-eval) rewrites the same column and bumps
the same way.
"""

from unittest.mock import MagicMock, patch

from src.modules.posture_rules.services.posture_evaluation_service import (
    PostureEvaluationService,
)
from src.modules.posture_rules.services.sub_rule_management_service import (
    PostureSubRuleManagementService,
)

A1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
A2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02"

_EVAL = "src.modules.posture_rules.services.posture_evaluation_service"
_MGMT = "src.modules.posture_rules.services.sub_rule_management_service"


def _eval_service(scope_active=True):
    repo = MagicMock()
    repo.list_categories.return_value = [
        {"id": "secured", "applicable_categories": []},
    ]
    repo.list_enabled_sub_rules.return_value = []
    repo.get_category.return_value = {"id": "secured", "applicable_categories": []}
    engine = MagicMock()
    engine.asset_ids_matching.return_value = {A1, A2}
    db = MagicMock()
    db.execute_query.return_value = [{"pg_try_advisory_lock": True}]
    db.conn_manager.is_scope_active.return_value = scope_active
    svc = PostureEvaluationService(
        repository=repo, query_engine=engine, db_adapter=db, resolver=MagicMock(),
    )
    return svc, repo, db


class TestEvaluateAll:
    def test_request_path_bumps_after_the_scope_commits(self):
        svc, _repo, db = _eval_service()
        order = MagicMock()
        order.attach_mock(db.conn_manager.end_scope, "end_scope")
        with patch(f"{_EVAL}.has_request_context", return_value=True), \
                patch(f"{_EVAL}.asset_data_version.bump") as mock_bump:
            order.attach_mock(mock_bump, "bump")
            result = svc.evaluate_all()
        assert result["skipped"] is False
        names = [c[0] for c in order.mock_calls]
        assert names == ["end_scope", "bump"]

    def test_pipeline_path_never_unwinds_the_write_scope_and_does_not_bump(self):
        svc, _repo, db = _eval_service(scope_active=True)
        with patch(f"{_EVAL}.has_request_context", return_value=False), \
                patch(f"{_EVAL}.asset_data_version.bump") as mock_bump:
            result = svc.evaluate_all()
        assert result["skipped"] is False
        db.conn_manager.end_scope.assert_not_called()
        mock_bump.assert_not_called()

    def test_scope_free_path_bumps_directly(self):
        svc, _repo, db = _eval_service(scope_active=False)
        with patch(f"{_EVAL}.has_request_context", return_value=False), \
                patch(f"{_EVAL}.asset_data_version.bump") as mock_bump:
            svc.evaluate_all()
        mock_bump.assert_called_once_with()

    def test_a_skipped_evaluation_bumps_nothing(self):
        svc, _repo, db = _eval_service()
        db.execute_query.return_value = [{"pg_try_advisory_lock": False}]
        with patch(f"{_EVAL}.has_request_context", return_value=True), \
                patch(f"{_EVAL}.asset_data_version.bump") as mock_bump:
            result = svc.evaluate_all()
        assert result["skipped"] is True
        mock_bump.assert_not_called()


class TestEvaluateCategory:
    def test_request_path_bumps_after_the_scope_commits(self):
        svc, repo, db = _eval_service()
        repo.patch_asset_category_state.return_value = 2
        order = MagicMock()
        order.attach_mock(db.conn_manager.end_scope, "end_scope")
        with patch(f"{_EVAL}.has_request_context", return_value=True), \
                patch(f"{_EVAL}.asset_data_version.bump") as mock_bump:
            order.attach_mock(mock_bump, "bump")
            result = svc.evaluate_category("secured")
        assert result["skipped"] is False
        names = [c[0] for c in order.mock_calls]
        assert names == ["end_scope", "bump"]

    def test_an_unknown_category_wrote_nothing_and_bumps_nothing(self):
        svc, repo, _db = _eval_service()
        repo.get_category.return_value = None
        with patch(f"{_EVAL}.has_request_context", return_value=True), \
                patch(f"{_EVAL}.asset_data_version.bump") as mock_bump:
            svc.evaluate_category("secured")
        mock_bump.assert_not_called()


class TestClearCategoryState:
    """toggle/delete of the last enabled rule clears state instead of
    re-evaluating — the same assets.posture_rules write, so the same bump."""

    def _service(self):
        repo = MagicMock()
        row = {"id": "sr-1", "category_id": "secured"}
        repo.get_sub_rule.return_value = row
        repo.toggle_sub_rule.return_value = row
        repo.delete_sub_rule.return_value = True
        repo.count_enabled_sub_rules.return_value = 0
        return PostureSubRuleManagementService(repository=repo), repo

    def test_disable_last_bumps_after_the_scope_commits(self):
        svc, repo = self._service()
        order = MagicMock()
        order.attach_mock(repo.db.conn_manager.end_scope, "end_scope")
        with patch(f"{_MGMT}.asset_data_version.bump") as mock_bump:
            order.attach_mock(mock_bump, "bump")
            svc.toggle("sr-1", False, user_id="test-user")
        repo.clear_category_state.assert_called_once_with("secured")
        names = [c[0] for c in order.mock_calls]
        assert names == ["end_scope", "bump"]

    def test_delete_last_bumps_the_data_stamp(self):
        svc, repo = self._service()
        with patch(f"{_MGMT}.asset_data_version.bump") as mock_bump:
            svc.delete("sr-1", user_id="test-user")
        repo.clear_category_state.assert_called_once_with("secured")
        mock_bump.assert_called_once_with()

    def test_disable_with_remaining_rules_re_evals_instead_of_bumping_here(self):
        svc, repo = self._service()
        repo.count_enabled_sub_rules.return_value = 2
        with patch(f"{_MGMT}.asset_data_version.bump") as mock_bump:
            svc.toggle("sr-1", False, user_id="test-user")
        repo.clear_category_state.assert_not_called()
        # evaluator is None in this harness, so _maybe_reeval is a no-op and
        # the only bump source would be the clear path — which did not run.
        mock_bump.assert_not_called()
