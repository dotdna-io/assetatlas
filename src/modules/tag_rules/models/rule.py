# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pydantic models for Tag Rules system

These models define the data structures for tag rules, filters, execution history,
and alerts. They provide validation and serialization for API requests/responses.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_serializer, validator
from enum import Enum

from src.modules.fields.models import FieldId
from src.modules.fields.operators import (
    OperatorResolutionError,
    resolve as resolve_operator,
)
from src.modules.fields.types import FilterOperator


# =============================================================================
# Filter and Criteria Models
# =============================================================================

class FilterCondition(BaseModel):
    """A single filter condition for asset matching"""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "field_id": {"kind": "static", "name": "OS Normalized"},
                "operator": "contains",
                "value": "Windows Server"
            }
        },
    )
    field_id: FieldId
    operator: FilterOperator
    value: str | int | float | bool | list | None = None

    @field_serializer("operator")
    def _serialize_operator_as_code(self, operator: FilterOperator) -> str:
        """Persist the canonical registry code (e.g. 'neq'), not the enum's
        spaced display label ('not equals'), so a saved/edited rule stores
        codes — completing migration 109 (otherwise editing a rule re-writes
        labels and undoes the migration). Falls back to the label if it is
        somehow unresolvable (it always resolves for registry-backed ops)."""
        try:
            return resolve_operator(operator.value).code
        except OperatorResolutionError:
            return operator.value


class ComplianceCriteria(BaseModel):
    """Compliance evaluation criteria for compliance rules"""
    logic: Literal["all", "any"] = Field(
        ...,
        description="Logic operator: 'all' (AND) or 'any' (OR)"
    )
    conditions: List[FilterCondition] = Field(
        ...,
        min_items=1,
        description="List of conditions to evaluate"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "logic": "any",
                "conditions": [
                    {
                        "field_id": {"kind": "extended_attribute", "source": "Jamf Pro", "attribute": "softwareInstalled"},
                        "operator": "contains",
                        "value": "Defender"
                    },
                    {
                        "field_id": {"kind": "extended_attribute", "source": "Jamf Pro", "attribute": "softwareInstalled"},
                        "operator": "contains",
                        "value": "SentinelOne"
                    }
                ]
            }
        }


class DetectionConfig(BaseModel):
    """Alert detection configuration for compliance rules"""
    enabled: bool = Field(..., description="Whether detection is enabled")
    condition: Literal[
        "Pass rate below threshold",
        "Fail rate above threshold",
        "Pass count below threshold",
        "Fail count above threshold"
    ] = Field(..., description="Condition that triggers an alert")
    threshold: float = Field(..., ge=0, description="Threshold value (percentage or count)")
    severity: Literal["Low", "Medium", "High", "Critical"] = Field(
        ...,
        description="Alert severity level"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "enabled": True,
                "condition": "Pass rate below threshold",
                "threshold": 90,
                "severity": "High"
            }
        }


# =============================================================================
# Tag Configuration Models
# =============================================================================

class ComplianceTagsConfig(BaseModel):
    """Tag configuration for compliance rules"""
    pass_: str = Field(..., alias="pass", description="Tag name for passing assets")
    fail: str = Field(..., description="Tag name for failing assets")

    class Config:
        populate_by_name = True  # Allow both 'pass' and 'pass_'
        json_schema_extra = {
            "example": {
                "pass": "AV-Protected",
                "fail": "AV-Missing"
            }
        }


class ClassificationTagsConfig(BaseModel):
    """Tag configuration for classification rules"""
    label: str = Field(..., description="Tag label/name")
    color: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$", description="Hex color code")

    class Config:
        json_schema_extra = {
            "example": {
                "label": "Finance-Network",
                "color": "#6366f1"
            }
        }


# =============================================================================
# Tag Rule Models
# =============================================================================

class TagRuleBase(BaseModel):
    """Base model for tag rule (common fields)"""
    name: str = Field(..., min_length=1, max_length=255, description="Rule name")
    description: Optional[str] = Field(None, description="Rule description")
    type: Literal["compliance", "classification"] = Field(..., description="Rule type")
    enabled: bool = Field(True, description="Whether rule is enabled")
    target_filters: List[FilterCondition] = Field(
        ...,
        min_items=1,
        description="Filters to match target assets"
    )


class TagRuleCreate(TagRuleBase):
    """Model for creating a new tag rule"""
    tags: Dict[str, Any] = Field(..., description="Tag configuration (structure depends on type)")
    compliance_criteria: Optional[ComplianceCriteria] = Field(
        None,
        description="Compliance criteria (required for compliance rules)"
    )
    detection_config: Optional[DetectionConfig] = Field(
        None,
        description="Detection/alert configuration (optional for compliance rules)"
    )

    @validator("compliance_criteria")
    def compliance_rules_need_criteria(cls, v, values):
        """Validate that compliance rules have compliance criteria"""
        if values.get("type") == "compliance" and v is None:
            raise ValueError("Compliance rules must have compliance_criteria")
        if values.get("type") == "classification" and v is not None:
            raise ValueError("Classification rules cannot have compliance_criteria")
        return v

    @validator("tags")
    def validate_tags_structure(cls, v, values):
        """Validate tags structure based on rule type"""
        rule_type = values.get("type")
        if rule_type == "compliance":
            if "pass" not in v or "fail" not in v:
                raise ValueError("Compliance rules must have 'pass' and 'fail' tags")
        elif rule_type == "classification":
            if "label" not in v or "color" not in v:
                raise ValueError("Classification rules must have 'label' and 'color'")
            # Validate color format
            color = v.get("color", "")
            if not color.startswith("#") or len(color) != 7:
                raise ValueError("Color must be in hex format (#RRGGBB)")
        return v

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "name": "Windows Server Classification",
                    "description": "Tags all Windows Server assets",
                    "type": "classification",
                    "enabled": True,
                    "target_filters": [
                        {
                            "field_id": {"kind": "static", "name": "OS Normalized"},
                            "operator": "contains",
                            "value": "Windows Server"
                        }
                    ],
                    "tags": {
                        "label": "Windows-Server",
                        "color": "#0078d4"
                    }
                },
                {
                    "name": "Antivirus Protection Check",
                    "description": "Checks if assets have AV software",
                    "type": "compliance",
                    "enabled": True,
                    "target_filters": [
                        {
                            "field_id": {"kind": "static", "name": "Asset Type"},
                            "operator": "equals",
                            "value": "workstation"
                        }
                    ],
                    "tags": {
                        "pass": "AV-Protected",
                        "fail": "AV-Missing"
                    },
                    "compliance_criteria": {
                        "logic": "any",
                        "conditions": [
                            {
                                "field_id": {"kind": "extended_attribute", "source": "Jamf Pro", "attribute": "softwareInstalled"},
                                "operator": "contains",
                                "value": "Defender"
                            }
                        ]
                    },
                    "detection_config": {
                        "enabled": True,
                        "condition": "Pass rate below threshold",
                        "threshold": 90,
                        "severity": "High"
                    }
                }
            ]
        }


class TagRuleUpdate(BaseModel):
    """Model for updating an existing tag rule"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    enabled: Optional[bool] = None
    target_filters: Optional[List[FilterCondition]] = Field(None, min_items=1)
    tags: Optional[Dict[str, Any]] = None
    compliance_criteria: Optional[ComplianceCriteria] = None
    detection_config: Optional[DetectionConfig] = None

    class Config:
        json_schema_extra = {
            "example": {
                "enabled": False,
                "description": "Updated description"
            }
        }


class TagRule(TagRuleBase):
    """Complete tag rule model (for responses)"""
    id: str = Field(..., description="Rule ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    last_modified: datetime = Field(..., description="Last modification timestamp")
    last_executed_at: Optional[datetime] = Field(None, description="Last execution timestamp")
    execution_count: int = Field(0, description="Total number of executions")
    last_execution_duration_ms: Optional[int] = Field(None, description="Last execution duration in ms")
    last_execution_assets_matched: int = Field(0, description="Assets matched in last execution")
    last_execution_assets_passed: Optional[int] = Field(None, description="Assets passed in last execution (compliance only)")
    last_execution_assets_failed: Optional[int] = Field(None, description="Assets failed in last execution (compliance only)")

    tags: Dict[str, Any] = Field(..., description="Tag configuration")
    compliance_criteria: Optional[ComplianceCriteria] = None
    detection_config: Optional[DetectionConfig] = None

    class Config:
        from_attributes = True  # For SQLAlchemy models
        json_schema_extra = {
            "example": {
                "id": "rule_1234567890_abc123",
                "name": "Antivirus Protection Check",
                "description": "Checks if assets have AV software",
                "type": "compliance",
                "enabled": True,
                "created_at": "2025-10-31T12:00:00Z",
                "last_modified": "2025-10-31T12:00:00Z",
                "last_executed_at": "2025-10-31T15:00:00Z",
                "execution_count": 42,
                "last_execution_duration_ms": 2340,
                "last_execution_assets_matched": 127,
                "last_execution_assets_passed": 104,
                "last_execution_assets_failed": 23,
                "target_filters": [
                    {
                        "field_id": {"kind": "static", "name": "OS Normalized"},
                        "operator": "contains",
                        "value": "Windows"
                    }
                ],
                "tags": {
                    "pass": "AV-Protected",
                    "fail": "AV-Missing"
                },
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [
                        {
                            "field_id": {"kind": "extended_attribute", "source": "Jamf Pro", "attribute": "softwareInstalled"},
                            "operator": "contains",
                            "value": "Defender"
                        }
                    ]
                },
                "detection_config": {
                    "enabled": True,
                    "condition": "Pass rate below threshold",
                    "threshold": 90,
                    "severity": "High"
                }
            }
        }


class TagRulesList(BaseModel):
    """Response model for listing tag rules"""
    rules: List[TagRule]
    total: int
    page: int = 1
    limit: int = 50

    class Config:
        json_schema_extra = {
            "example": {
                "rules": [],
                "total": 15,
                "page": 1,
                "limit": 50
            }
        }


# =============================================================================
# Execution History Models
# =============================================================================

class ExecutionHistory(BaseModel):
    """Rule execution history record"""
    id: int
    rule_id: str
    executed_at: datetime
    execution_time_ms: Optional[int] = None
    status: Literal["success", "failed", "partial"]
    error_message: Optional[str] = None
    assets_matched: int
    assets_passed: Optional[int] = None
    assets_failed: Optional[int] = None
    tags_applied: int
    tags_removed: int
    triggered_by: Literal["scheduled", "manual", "api", "asset_generation_pipeline"]
    triggered_by_user: Optional[str] = None
    generation_run_id: Optional[str] = None

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": 12345,
                "rule_id": "rule_1234567890_abc123",
                "executed_at": "2025-10-31T15:00:00Z",
                "execution_time_ms": 2340,
                "status": "success",
                "error_message": None,
                "assets_matched": 127,
                "assets_passed": 104,
                "assets_failed": 23,
                "tags_applied": 127,
                "tags_removed": 5,
                "triggered_by": "asset_generation_pipeline",
                "triggered_by_user": None,
                "generation_run_id": "550e8400-e29b-41d4-a716-446655440000"
            }
        }


class ExecutionHistoryList(BaseModel):
    """Response model for execution history list"""
    executions: List[ExecutionHistory]
    total: int
    page: int = 1
    limit: int = 50


# =============================================================================
# Preview/Count Models
# =============================================================================

class PreviewCountRequest(BaseModel):
    """Request model for preview/count endpoint"""
    target_filters: List[FilterCondition] = Field(..., min_items=1)
    compliance_criteria: Optional[ComplianceCriteria] = None

    class Config:
        json_schema_extra = {
            "example": {
                "target_filters": [
                    {
                        "field_id": {"kind": "static", "name": "OS Normalized"},
                        "operator": "contains",
                        "value": "Windows"
                    }
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [
                        {
                            "field_id": {"kind": "extended_attribute", "source": "Jamf Pro", "attribute": "softwareInstalled"},
                            "operator": "contains",
                            "value": "Defender"
                        }
                    ]
                }
            }
        }


class PreviewCountResponse(BaseModel):
    """Response model for preview/count endpoint"""
    total_matching: int = Field(..., description="Total assets matching target filters")
    pass_count: Optional[int] = Field(None, description="Assets passing compliance (if applicable)")
    fail_count: Optional[int] = Field(None, description="Assets failing compliance (if applicable)")
    execution_time_ms: int = Field(..., description="Query execution time in milliseconds")

    class Config:
        json_schema_extra = {
            "example": {
                "total_matching": 127,
                "pass_count": 104,
                "fail_count": 23,
                "execution_time_ms": 45
            }
        }


# =============================================================================
# Alert Models
# =============================================================================

class DetectionAlert(BaseModel):
    """Detection alert model"""
    id: int
    rule_id: str
    execution_id: Optional[int] = None
    triggered_at: datetime
    resolved_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    condition: str
    threshold_value: Optional[float] = None
    actual_value: Optional[float] = None
    severity: Literal["Low", "Medium", "High", "Critical"]
    status: Literal["active", "resolved", "suppressed"]
    message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    # Optionally include rule details
    rule: Optional[TagRule] = None

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": 1,
                "rule_id": "rule_1234567890_abc123",
                "execution_id": 12345,
                "triggered_at": "2025-10-31T15:00:00Z",
                "resolved_at": None,
                "acknowledged_at": None,
                "acknowledged_by": None,
                "condition": "Pass rate below threshold",
                "threshold_value": 90.0,
                "actual_value": 70.5,
                "severity": "High",
                "status": "active",
                "message": "Compliance pass rate dropped below threshold",
                "metadata": None
            }
        }


class AlertsList(BaseModel):
    """Response model for alerts list"""
    alerts: List[DetectionAlert]
    total: int
    page: int = 1
    limit: int = 50


class ResolveAlertRequest(BaseModel):
    """Request model for resolving an alert"""
    acknowledged_by: str = Field(..., min_length=1)
    notes: Optional[str] = Field(None, description="Optional resolution notes")

    class Config:
        json_schema_extra = {
            "example": {
                "acknowledged_by": "admin@example.com",
                "notes": "Issue has been resolved after patching"
            }
        }


# =============================================================================
# Field Options Models
# =============================================================================

class FieldOption(BaseModel):
    """Field option metadata"""
    name: str
    data_type: str
    operators: List[str]


class FieldOptionsResponse(BaseModel):
    """Response model for field options endpoint"""
    target_fields: List[FieldOption]
    compliance_fields: List[FieldOption]
    advanced_fields: Optional[List[FieldOption]] = None

    class Config:
        json_schema_extra = {
            "example": {
                "target_fields": [
                    {
                        "name": "OS Type",
                        "data_type": "string",
                        "operators": ["equals", "not equals", "contains", "not contains"]
                    }
                ],
                "compliance_fields": [
                    {
                        "name": "Software Installed",
                        "data_type": "string",
                        "operators": ["contains", "not contains"]
                    }
                ],
                "advanced_fields": []
            }
        }


# =============================================================================
# Execution Request Models
# =============================================================================

class ExecuteRuleRequest(BaseModel):
    """Request model for manual rule execution"""
    async_: bool = Field(
        False,
        alias="async",
        description="Run asynchronously (return immediately with job ID)"
    )

    class Config:
        populate_by_name = True


class ExecuteRuleResponse(BaseModel):
    """Response model for rule execution"""
    execution_id: int
    status: str
    assets_matched: Optional[int] = None
    assets_passed: Optional[int] = None
    assets_failed: Optional[int] = None
    tags_applied: Optional[int] = None
    execution_time_ms: Optional[int] = None
    message: Optional[str] = None

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "execution_id": 12345,
                    "status": "success",
                    "assets_matched": 127,
                    "assets_passed": 104,
                    "assets_failed": 23,
                    "tags_applied": 127,
                    "execution_time_ms": 2340
                },
                {
                    "execution_id": 12346,
                    "status": "queued",
                    "message": "Rule execution queued for processing"
                }
            ]
        }
