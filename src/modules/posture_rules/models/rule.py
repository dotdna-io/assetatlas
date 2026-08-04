# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models and shared constants for the posture_rules system."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from src.modules.fields.models import FieldId
from src.modules.tag_rules.models.rule import FilterCondition, ComplianceCriteria


class PostureState(str, Enum):
    """Per-asset per-category state stored in `assets.posture_rules`.

    `str` mixin keeps JSON serialisation as a plain string.
    """
    PASS = "true"
    FAIL = "false"
    NA = "n/a"


class SubRuleAction(str, Enum):
    """Action written to posture_sub_rule_history.action."""
    CREATE = "create"
    UPDATE = "update"
    TOGGLE = "toggle"
    DELETE = "delete"


# Map compliance_criteria.logic ('all'/'any') -> SQL operator ('AND'/'OR').
_CRITERIA_LOGIC_TO_SQL = {"all": "AND", "any": "OR"}


def criteria_logic_to_sql(logic: str) -> str:
    """Translate a compliance_criteria.logic value to its SQL operator."""
    return _CRITERIA_LOGIC_TO_SQL.get(logic, "AND")


# Valid asset-category tokens — must match CategorySelector.VALID_CATEGORIES.
# Duplicated here to keep posture_rules module independent of asset_generation.
# If the asset generation module's list changes, update both (covered by a guard
# test in tests/test_models.py).
VALID_APPLICABLE_CATEGORIES = {
    "server", "workstation", "mobile", "network", "iot",
    "ot", "storage", "miscellaneous", "unknown",
}


# =============================================================================
# Categories
# =============================================================================

class AssetCategoryCoverage(BaseModel):
    covered: int  # assets matching the criteria (compliant / pass)
    total: int    # all assets in this asset category (pass + fail + n/a)


class PostureCategoryStats(BaseModel):
    pass_: int = Field(..., alias="pass")
    fail: int
    n_a: int
    by_asset_category: Dict[str, AssetCategoryCoverage] = Field(default_factory=dict)

    class Config:
        populate_by_name = True


class PostureSubRuleLastEdit(BaseModel):
    action: str
    changed_by: str
    changed_by_username: Optional[str] = None
    at: datetime


class PostureSubRuleResponse(BaseModel):
    id: str
    category_id: str
    name: str
    description: Optional[str] = None
    enabled: bool
    target_filters: List[FilterCondition]
    compliance_criteria: ComplianceCriteria
    output_mode: Optional[Literal["field", "text"]] = None
    # JSONB dict shape stored in the `output_value` column. Either
    # {"mode": "field", "field_id": {<FieldId>}} or {"mode": "text", "text": "..."}.
    # See sub_rule_management_service._serialise_output_value.
    output_value: Optional[Dict[str, Any]] = None
    display_order: int = 0
    last_edit: Optional[PostureSubRuleLastEdit] = None
    created_at: datetime
    updated_at: datetime


class PostureSubRuleHistoryEntry(BaseModel):
    id: str
    sub_rule_id: str
    category_id: str
    action: str
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    changed_by: str
    changed_by_username: Optional[str] = None
    created_at: datetime


class PostureCategoryResponse(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    applicable_categories: List[str]
    display_order: int
    last_evaluated_at: Optional[datetime] = None
    sub_rules: List[PostureSubRuleResponse] = Field(default_factory=list)
    stats: PostureCategoryStats


class PostureCategoryListResponse(BaseModel):
    categories: List[PostureCategoryResponse]


class PostureCategoryUpdateRequest(BaseModel):
    """PATCH payload for posture_categories. Only applicable_categories is editable."""
    applicable_categories: List[str]

    @field_validator("applicable_categories", mode="before")
    @classmethod
    def _normalise(cls, v: List[str]) -> List[str]:
        if v is None:
            return []
        lowered = [str(x).strip().lower() for x in v]
        # Preserve input order while deduplicating.
        seen: set = set()
        out: List[str] = []
        for x in lowered:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    @field_validator("applicable_categories")
    @classmethod
    def _validate_known(cls, v: List[str]) -> List[str]:
        unknown = [x for x in v if x not in VALID_APPLICABLE_CATEGORIES]
        if unknown:
            raise ValueError(
                f"Unknown asset category tokens: {unknown}. "
                f"Valid: {sorted(VALID_APPLICABLE_CATEGORIES)}"
            )
        return v


# =============================================================================
# Sub-rule CRUD
# =============================================================================

class PostureSubRuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    enabled: bool = True
    target_filters: List[FilterCondition] = Field(..., min_items=1)
    compliance_criteria: ComplianceCriteria
    output_mode: Optional[Literal["field", "text"]] = None
    output_field_id: Optional[FieldId] = None
    output_text: Optional[str] = None

    @model_validator(mode="after")
    def _validate_output_consistency(self) -> "PostureSubRuleBase":
        if self.output_mode == "field" and self.output_field_id is None:
            raise ValueError("output_field_id is required when output_mode is 'field'")
        if self.output_mode == "text" and not self.output_text:
            raise ValueError("output_text is required when output_mode is 'text'")
        return self


class PostureSubRuleCreate(PostureSubRuleBase):
    pass


class PostureSubRuleUpdate(PostureSubRuleBase):
    pass


class PostureSubRuleToggleRequest(BaseModel):
    enabled: bool


class PostureSubRuleOrderRequest(BaseModel):
    """PUT payload for /api/posture-rules/{category_id}/sub-rules/order."""
    ordered_ids: List[str] = Field(..., min_length=1)


# =============================================================================
# Preview + execute
# =============================================================================

class PosturePreviewRequest(BaseModel):
    target_filters: List[FilterCondition] = Field(..., min_items=1)
    compliance_criteria: ComplianceCriteria


class PosturePreviewResponse(BaseModel):
    matched: int
    pass_: int = Field(..., alias="pass")
    fail: int

    class Config:
        populate_by_name = True


class PostureOutputPreviewRequest(BaseModel):
    target_filters: List[FilterCondition] = Field(..., min_length=1)
    compliance_criteria: ComplianceCriteria
    output_mode: Literal["field", "text"]
    # field mode: must be a typed FieldId — never interpolated directly into SQL.
    output_field_id: Optional[FieldId] = None
    # text mode: literal display string.
    output_text: Optional[str] = None

    @model_validator(mode="after")
    def _validate_output_consistency(self) -> "PostureOutputPreviewRequest":
        if self.output_mode == "field" and self.output_field_id is None:
            raise ValueError("output_field_id is required when output_mode is 'field'")
        if self.output_mode == "text" and not self.output_text:
            raise ValueError("output_text is required when output_mode is 'text'")
        return self


class PostureOutputPreviewSample(BaseModel):
    hostname: Optional[str] = None
    output: Optional[str] = None


class PostureOutputPreviewResponse(BaseModel):
    matched: int
    passed: int
    samples: List[PostureOutputPreviewSample]


class PostureExecuteResponse(BaseModel):
    updated_count: int
    duration_ms: int
    skipped: bool = False
