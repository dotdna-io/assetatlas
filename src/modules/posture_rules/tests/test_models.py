# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for posture_rules Pydantic models."""
import pytest
from pydantic import ValidationError

from src.modules.posture_rules.models.rule import (
    PostureSubRuleCreate,
    PostureSubRuleUpdate,
    PostureSubRuleToggleRequest,
    PosturePreviewRequest,
)


class TestPostureSubRuleCreate:
    def test_valid_payload(self):
        m = PostureSubRuleCreate(
            name="Windows workstation EDR",
            target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}],
            compliance_criteria={
                "logic": "any",
                "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "EDR"}],
            },
        )
        assert m.name == "Windows workstation EDR"
        assert m.enabled is True  # default
        assert m.description is None

    def test_missing_name(self):
        with pytest.raises(ValidationError):
            PostureSubRuleCreate(
                target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}],
                compliance_criteria={"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "x"}, "operator": "equals", "value": "y"}]},
            )

    def test_empty_target_filters_rejected(self):
        with pytest.raises(ValidationError):
            PostureSubRuleCreate(
                name="x",
                target_filters=[],
                compliance_criteria={"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "x"}, "operator": "equals", "value": "y"}]},
            )

    def test_compliance_criteria_requires_condition(self):
        with pytest.raises(ValidationError):
            PostureSubRuleCreate(
                name="x",
                target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}],
                compliance_criteria={"logic": "any", "conditions": []},
            )


class TestPostureSubRuleToggleRequest:
    def test_enabled_required(self):
        with pytest.raises(ValidationError):
            PostureSubRuleToggleRequest()

    def test_enabled_bool(self):
        m = PostureSubRuleToggleRequest(enabled=False)
        assert m.enabled is False


class TestPosturePreviewRequest:
    def test_valid_payload(self):
        m = PosturePreviewRequest(
            target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}],
            compliance_criteria={
                "logic": "all",
                "conditions": [{"field_id": {"kind": "static", "name": "x"}, "operator": "equals", "value": "y"}],
            },
        )
        assert m.target_filters[0].field_id.name == "Category"
        assert m.compliance_criteria.logic == "all"


class TestPostureCategoryUpdateRequest:
    def test_accepts_known_categories(self):
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        m = PostureCategoryUpdateRequest(applicable_categories=["server", "workstation"])
        assert m.applicable_categories == ["server", "workstation"]

    def test_rejects_unknown_category(self):
        import pytest
        from pydantic import ValidationError
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        with pytest.raises(ValidationError) as ei:
            PostureCategoryUpdateRequest(applicable_categories=["server", "toaster"])
        assert "toaster" in str(ei.value)

    def test_lowercases_input(self):
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        m = PostureCategoryUpdateRequest(applicable_categories=["Server", "WORKSTATION"])
        assert m.applicable_categories == ["server", "workstation"]

    def test_deduplicates(self):
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        m = PostureCategoryUpdateRequest(applicable_categories=["server", "server"])
        assert m.applicable_categories == ["server"]

    def test_allows_empty_list(self):
        from src.modules.posture_rules.models.rule import PostureCategoryUpdateRequest
        m = PostureCategoryUpdateRequest(applicable_categories=[])
        assert m.applicable_categories == []


def test_valid_applicable_categories_matches_category_selector():
    """If CategorySelector.VALID_CATEGORIES changes, update VALID_APPLICABLE_CATEGORIES."""
    from src.modules.asset_generation.domain.category_selector import CategorySelector
    from src.modules.posture_rules.models.rule import VALID_APPLICABLE_CATEGORIES
    assert VALID_APPLICABLE_CATEGORIES == CategorySelector.VALID_CATEGORIES
