# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock
import pytest
from datetime import datetime, timezone

from src.modules.posture_rules.services.sub_rule_management_service import (
    PostureSubRuleManagementService,
    CategoryNotFoundError,
    SubRuleNotFoundError,
)
from src.modules.posture_rules.models.rule import (
    PostureSubRuleCreate,
    PostureSubRuleUpdate,
)


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_category.return_value = {"id": "secured"}
    now = datetime.now(timezone.utc)
    r.create_sub_rule.return_value = {
        "id": "sr-1", "category_id": "secured", "name": "x", "description": None,
        "enabled": True,
        "target_filters": [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}],
        "compliance_criteria": {"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source"}, "operator": "equals", "value": "b"}]},
        "created_at": now, "updated_at": now,
    }
    r.update_sub_rule.return_value = r.create_sub_rule.return_value
    r.toggle_sub_rule.return_value = r.create_sub_rule.return_value
    r.delete_sub_rule.return_value = True
    r.get_sub_rule.return_value = r.create_sub_rule.return_value
    # Default: other enabled rules exist in the category — covers the common
    # path and keeps the stale-state cleanup out of the way unless a test
    # explicitly dials this to 0.
    r.count_enabled_sub_rules.return_value = 1
    return r


@pytest.fixture
def service(repo):
    return PostureSubRuleManagementService(repository=repo)


def _payload():
    return PostureSubRuleCreate(
        name="x",
        target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}],
        compliance_criteria={"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source"}, "operator": "equals", "value": "b"}]},
    )


class TestCreate:
    def test_creates_when_category_exists(self, service, repo):
        out = service.create("secured", _payload(), user_id="test-user")
        assert out["id"] == "sr-1"
        repo.create_sub_rule.assert_called_once()

    def test_404_when_category_missing(self, service, repo):
        repo.get_category.return_value = None
        with pytest.raises(CategoryNotFoundError):
            service.create("missing", _payload(), user_id="test-user")


class TestUpdate:
    def test_update_existing(self, service, repo):
        out = service.update("sr-1", PostureSubRuleUpdate(**_payload().model_dump()), user_id="test-user")
        assert out["id"] == "sr-1"

    def test_404_when_missing(self, service, repo):
        repo.update_sub_rule.return_value = None
        with pytest.raises(SubRuleNotFoundError):
            service.update("missing", PostureSubRuleUpdate(**_payload().model_dump()), user_id="test-user")


class TestToggle:
    def test_toggle_existing(self, service, repo):
        out = service.toggle("sr-1", False, user_id="test-user")
        assert out["id"] == "sr-1"
        repo.toggle_sub_rule.assert_called_once_with("sr-1", False)

    def test_404_when_missing(self, service, repo):
        repo.toggle_sub_rule.return_value = None
        with pytest.raises(SubRuleNotFoundError):
            service.toggle("missing", True, user_id="test-user")

    def test_disable_last_clears_category_state(self, service, repo):
        repo.count_enabled_sub_rules.return_value = 0
        service.toggle("sr-1", False, user_id="test-user")
        repo.clear_category_state.assert_called_once_with("secured")

    def test_disable_with_others_keeps_state(self, service, repo):
        repo.count_enabled_sub_rules.return_value = 2
        service.toggle("sr-1", False, user_id="test-user")
        repo.clear_category_state.assert_not_called()

    def test_enable_never_clears_state(self, service, repo):
        # Even with zero other enabled rules, enabling can't orphan state.
        repo.count_enabled_sub_rules.return_value = 0
        service.toggle("sr-1", True, user_id="test-user")
        repo.clear_category_state.assert_not_called()


class TestDelete:
    def test_delete_existing(self, service, repo):
        assert service.delete("sr-1", user_id="test-user") is None

    def test_404_when_missing(self, service, repo):
        repo.delete_sub_rule.return_value = False
        with pytest.raises(SubRuleNotFoundError):
            service.delete("missing", user_id="test-user")

    def test_delete_last_clears_category_state(self, service, repo):
        repo.count_enabled_sub_rules.return_value = 0
        service.delete("sr-1", user_id="test-user")
        repo.clear_category_state.assert_called_once_with("secured")

    def test_delete_with_others_keeps_state(self, service, repo):
        repo.count_enabled_sub_rules.return_value = 1
        service.delete("sr-1", user_id="test-user")
        repo.clear_category_state.assert_not_called()


class TestUpdateApplicableCategories:
    def test_updates_when_category_exists(self, service, repo):
        repo.update_category_applicable_categories.return_value = {
            "id": "secured",
            "applicable_categories": ["server", "workstation"],
        }
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        out = service.update_applicable_categories(
            "secured",
            PostureCategoryUpdateRequest(applicable_categories=["server", "workstation"]),
        )
        assert out["applicable_categories"] == ["server", "workstation"]
        repo.update_category_applicable_categories.assert_called_once_with(
            "secured", ["server", "workstation"],
        )

    def test_404_when_category_missing(self, service, repo):
        repo.update_category_applicable_categories.return_value = None
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        import pytest
        with pytest.raises(CategoryNotFoundError):
            service.update_applicable_categories(
                "missing",
                PostureCategoryUpdateRequest(applicable_categories=["server"]),
            )


class TestApplicableCategoriesBumpCatalogStamp:
    """Posture category changes feed the FieldCatalog's posture entries, so a
    category update bumps the shared version stamp (#1413) to rebuild every
    worker's catalog; a missing category changes nothing and must not bump."""

    _BUMP = ("src.modules.posture_rules.services.sub_rule_management_service"
             ".catalog_version.bump")

    def test_update_bumps_the_field_catalog_stamp(self, service, repo):
        repo.update_category_applicable_categories.return_value = {
            "id": "secured",
            "applicable_categories": ["server"],
        }
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        from unittest.mock import patch
        with patch(self._BUMP) as mock_bump:
            service.update_applicable_categories(
                "secured",
                PostureCategoryUpdateRequest(applicable_categories=["server"]),
            )
        mock_bump.assert_called_once_with()

    def test_missing_category_does_not_bump(self, service, repo):
        repo.update_category_applicable_categories.return_value = None
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        from unittest.mock import patch
        with patch(self._BUMP) as mock_bump:
            with pytest.raises(CategoryNotFoundError):
                service.update_applicable_categories(
                    "missing",
                    PostureCategoryUpdateRequest(applicable_categories=["server"]),
                )
        mock_bump.assert_not_called()


class TestFieldNameValidation:
    """Field shape validation is now handled by Pydantic at parse time.

    The service no longer runs _validate_fields — it delegates to Pydantic
    (discriminated-union parse) for shape errors and to FieldResolver (via the
    query engine) for liveness errors at query time.
    """

    def test_pydantic_rejects_missing_field_id(self):
        """A filter condition dict without field_id is rejected by Pydantic."""
        from pydantic import ValidationError as PydanticValidationError
        from src.modules.posture_rules.models.rule import PostureSubRuleCreate
        with pytest.raises(PydanticValidationError):
            PostureSubRuleCreate(
                name="bad",
                target_filters=[{"operator": "equals", "value": "x"}],  # missing field_id
                compliance_criteria={"logic": "any", "conditions": [
                    {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
                ]},
            )

    def test_pydantic_rejects_unknown_field_id_kind(self):
        """A field_id with an unrecognised kind is rejected by Pydantic."""
        from pydantic import ValidationError as PydanticValidationError
        from src.modules.posture_rules.models.rule import PostureSubRuleCreate
        with pytest.raises(PydanticValidationError):
            PostureSubRuleCreate(
                name="bad",
                target_filters=[{"field_id": {"kind": "bogus_kind"}, "operator": "equals", "value": "x"}],
                compliance_criteria={"logic": "any", "conditions": [
                    {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
                ]},
            )

    def test_text_mode_output_not_rejected(self, service, repo):
        """output_mode='text' with output_text is accepted — not a SQL injection risk."""
        from src.modules.posture_rules.models.rule import PostureSubRuleCreate
        repo.create_sub_rule.return_value = {
            "id": "sr-1", "category_id": "secured", "name": "ok", "description": None,
            "enabled": True,
            "target_filters": [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}],
            "compliance_criteria": {"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"}]},
            "output_mode": "text", "output_value": {"mode": "text", "text": "Arbitrary free text"},
            "created_at": None, "updated_at": None,
        }
        payload = PostureSubRuleCreate(
            name="ok",
            target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}],
            compliance_criteria={"logic": "any", "conditions": [
                {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
            ]},
            output_mode="text",
            output_text="Arbitrary free text",
        )
        # Should NOT raise
        service.create("secured", payload, user_id="test-user")

    def test_output_field_id_required_in_field_mode(self):
        """field mode without output_field_id is rejected at Pydantic validation time."""
        from pydantic import ValidationError as PydanticValidationError
        from src.modules.posture_rules.models.rule import PostureSubRuleCreate
        with pytest.raises(PydanticValidationError):
            PostureSubRuleCreate(
                name="bad",
                target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}],
                compliance_criteria={"logic": "any", "conditions": [
                    {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
                ]},
                output_mode="field",
                # missing output_field_id
            )


class TestReorderSubRules:
    def test_reorder_success(self, service, repo):
        repo.reorder_sub_rules.return_value = True
        service.reorder_sub_rules("secured", ["a", "b"])
        repo.reorder_sub_rules.assert_called_once_with("secured", ["a", "b"])

    def test_reorder_404_unknown_category(self, service, repo):
        import pytest
        repo.get_category.return_value = None
        with pytest.raises(CategoryNotFoundError):
            service.reorder_sub_rules("missing", ["a"])

    def test_reorder_404_unknown_subrule(self, service, repo):
        import pytest
        repo.reorder_sub_rules.return_value = False
        with pytest.raises(SubRuleNotFoundError):
            service.reorder_sub_rules("secured", ["bogus-id"])


class TestHistoryWrites:
    def test_create_writes_history(self, service, repo):
        payload = _payload()
        service.create("secured", payload, user_id="user-1", username="alice")
        repo.insert_sub_rule_history.assert_called_once()
        kwargs = repo.insert_sub_rule_history.call_args.kwargs
        assert kwargs["action"] == "create"
        assert kwargs["changed_by"] == "user-1"
        assert kwargs["changed_by_username"] == "alice"
        assert kwargs["before_state"] is None
        assert kwargs["after_state"] is not None

    def test_update_writes_history_with_before_and_after(self, service, repo):
        repo.get_sub_rule.return_value = {"id": "sr-1", "category_id": "secured", "name": "old"}
        service.update("sr-1", PostureSubRuleUpdate(**_payload().model_dump()), user_id="user-1")
        repo.insert_sub_rule_history.assert_called_once()
        kwargs = repo.insert_sub_rule_history.call_args.kwargs
        assert kwargs["action"] == "update"
        assert kwargs["before_state"] is not None
        assert kwargs["after_state"] is not None

    def test_toggle_writes_history(self, service, repo):
        repo.get_sub_rule.return_value = {"id": "sr-1", "category_id": "secured", "enabled": True}
        service.toggle("sr-1", False, user_id="user-1")
        repo.insert_sub_rule_history.assert_called_once()
        assert repo.insert_sub_rule_history.call_args.kwargs["action"] == "toggle"

    def test_delete_writes_history_with_before(self, service, repo):
        repo.get_sub_rule.return_value = {"id": "sr-1", "category_id": "secured"}
        service.delete("sr-1", user_id="user-1")
        repo.insert_sub_rule_history.assert_called_once()
        kwargs = repo.insert_sub_rule_history.call_args.kwargs
        assert kwargs["action"] == "delete"
        assert kwargs["after_state"] is None

    def test_history_write_failure_does_not_break_primary_op(self, service, repo):
        """If audit write fails, the primary CRUD still succeeds."""
        repo.insert_sub_rule_history.side_effect = RuntimeError("audit table unavailable")
        # Should not raise
        service.create("secured", _payload(), user_id="user-1")
        repo.create_sub_rule.assert_called_once()
