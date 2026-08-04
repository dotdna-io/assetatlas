# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models for alert rule requests and responses."""

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertRuleCondition(BaseModel):
    field: str
    op: str
    # List operators (any/all/none) and between send a string[]; scalar/empty ops
    # send a string. A legacy stored rule may still hold a comma string. (D-1)
    value: str | list[str] = ''


class ComplianceCriteria(BaseModel):
    logic: Literal['all', 'any']
    conditions: list[AlertRuleCondition]


class AlertRuleThreshold(BaseModel):
    # 'changes_by' is explicitly rejected (v1 scope — needs historical state).
    condition: Literal['falls_below', 'exceeds']
    value: float
    unit: Literal['percent', 'number']


class AnomalyThreshold(BaseModel):
    """Partial patch for a system anomaly rule's threshold jsonb (#878 Gap 1).
    All fields optional — only provided keys are merged into the stored threshold,
    so a partial edit never drops other params. extra='forbid' rejects typos
    (detectors read params with .get() defaults, so an unknown key would silently
    do nothing). Tunes all system anomaly detectors (robust-z, count/window, ML)."""
    model_config = ConfigDict(extra='forbid')

    severity: Optional[Literal['critical', 'high', 'medium', 'low']] = None
    # ML detectors (ml_newcomer / ml_behavior)
    pct_medium: Optional[float] = Field(default=None, ge=0, le=100)
    pct_high: Optional[float] = Field(default=None, ge=0, le=100)
    min_fit_rows: Optional[int] = Field(default=None, ge=1)
    max_fit_rows: Optional[int] = Field(default=None, ge=1)
    fit_budget_seconds: Optional[float] = Field(default=None, ge=0)
    top_k_categories: Optional[int] = Field(default=None, ge=1)
    # robust-z detectors
    z_medium: Optional[float] = Field(default=None, ge=0)
    z_high: Optional[float] = Field(default=None, ge=0)
    z_critical: Optional[float] = Field(default=None, ge=0)
    # counts / windows / percentages shared across detectors
    min_count: Optional[int] = Field(default=None, ge=1)
    min_pct: Optional[float] = Field(default=None, ge=0, le=100)
    min_pp: Optional[float] = Field(default=None, ge=0, le=100)
    systemic_pct: Optional[float] = Field(default=None, ge=0, le=100)
    window_runs: Optional[int] = Field(default=None, ge=1)
    window_days: Optional[int] = Field(default=None, ge=1)
    min_runs: Optional[int] = Field(default=None, ge=1)
    min_samples: Optional[int] = Field(default=None, ge=1)
    min_seasonal_samples: Optional[int] = Field(default=None, ge=1)
    min_attrs: Optional[int] = Field(default=None, ge=1)
    min_flaps: Optional[int] = Field(default=None, ge=1)
    min_seen: Optional[int] = Field(default=None, ge=1)
    stability_days: Optional[int] = Field(default=None, ge=1)
    settle_min_days: Optional[int] = Field(default=None, ge=1)
    settle_max_days: Optional[int] = Field(default=None, ge=1)


class CreateAlertRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ''
    type: Literal['security', 'compliance', 'operational', 'data_quality']
    severity: Literal['critical', 'high', 'medium', 'low']
    target_filters: list[AlertRuleCondition]
    compliance_criteria: Optional[ComplianceCriteria] = None
    threshold: Optional[AlertRuleThreshold] = None
    silence_duration: Optional[int] = Field(default=None, ge=1, le=10080)  # 1..7 days in minutes
    enabled: bool = True

    @field_validator('target_filters')
    @classmethod
    def require_at_least_one_target_filter(cls, v):
        if not v:
            raise ValueError('target_filters cannot be empty')
        return v


class UpdateAlertRuleRequest(BaseModel):
    """All fields optional — only provided fields are updated."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    type: Optional[Literal['security', 'compliance', 'operational', 'data_quality']] = None
    severity: Optional[Literal['critical', 'high', 'medium', 'low']] = None
    target_filters: Optional[list[AlertRuleCondition]] = None
    compliance_criteria: Optional[ComplianceCriteria] = None
    threshold: Optional[AlertRuleThreshold] = None
    silence_duration: Optional[int] = Field(default=None, ge=1, le=10080)
    enabled: Optional[bool] = None

    @field_validator('target_filters')
    @classmethod
    def reject_empty_target_filters(cls, v):
        if v is not None and len(v) == 0:
            raise ValueError('target_filters cannot be empty')
        return v


class ToggleAlertRuleRequest(BaseModel):
    enabled: bool


class PreviewAlertRuleRequest(BaseModel):
    target_filters: list[AlertRuleCondition]
    compliance_criteria: Optional[ComplianceCriteria] = None

    @field_validator('target_filters')
    @classmethod
    def require_at_least_one_target_filter(cls, v):
        if not v:
            raise ValueError('target_filters cannot be empty')
        return v
