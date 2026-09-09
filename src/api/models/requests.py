# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Request Pydantic Models

Request body and query parameter models for all API endpoints.

Models are organized by domain:
- Tag Rules (11 endpoints)
- Assets (3 endpoints)
- Dashboard (7 endpoints)
- Integrations (38 endpoints)
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, validator, field_validator, EmailStr
from enum import Enum

from src.modules.fields.models import FieldId
from src.modules.fields.encoding import decode as decode_field_id


# =============================================================================
# Common Models
# =============================================================================

class PaginationParams(BaseModel):
    """Common pagination query parameters"""
    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    limit: int = Field(default=50, ge=1, le=10000, description="Items per page")
    offset: Optional[int] = Field(default=None, ge=0, description="Offset (calculated if not provided)")

    @validator('offset', always=True)
    def calculate_offset(cls, v, values):
        """Calculate offset from page and limit if not provided"""
        if v is not None:
            return v
        page = values.get('page', 1)
        limit = values.get('limit', 50)
        return (page - 1) * limit


# =============================================================================
# Tag Rules Models (11 endpoints)
# =============================================================================

class TagRuleType(str, Enum):
    """Tag rule types"""
    classification = "classification"
    assignment = "assignment"


class FilterOperator(str, Enum):
    """Filter operators for tag rules"""
    equals = "equals"
    not_equals = "not_equals"
    contains = "contains"
    not_contains = "not_contains"
    starts_with = "starts_with"
    ends_with = "ends_with"
    in_list = "in"
    not_in_list = "not_in"
    greater_than = "greater_than"
    less_than = "less_than"
    is_null = "is_null"
    is_not_null = "is_not_null"


class TagInfo(BaseModel):
    """Tag information"""
    label: str = Field(min_length=1, max_length=100, description="Tag label")
    color: str = Field(pattern=r'^#[0-9A-Fa-f]{6}$', description="Hex color code")


class TargetFilter(BaseModel):
    """Target filter for tag rules"""
    operator: FilterOperator
    value: Optional[Any] = None


class TagRuleCreate(BaseModel):
    """Request model for creating a tag rule"""
    name: str = Field(min_length=1, max_length=200, description="Rule name")
    type: TagRuleType = Field(description="Rule type")
    enabled: bool = Field(default=True, description="Whether rule is enabled")
    target_filters: Dict[str, TargetFilter] = Field(description="Target filters")
    tags: TagInfo = Field(description="Tag information")
    description: Optional[str] = Field(default=None, max_length=1000, description="Rule description")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Windows Server Classification",
                "type": "classification",
                "enabled": True,
                "target_filters": {
                    "os_type": {
                        "operator": "equals",
                        "value": "Windows"
                    },
                    "asset_type": {
                        "operator": "equals",
                        "value": "server"
                    }
                },
                "tags": {
                    "label": "Windows-Server",
                    "color": "#0078d4"
                },
                "description": "Automatically tag Windows servers"
            }
        }


class TagRuleUpdate(BaseModel):
    """Request model for updating a tag rule"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    enabled: Optional[bool] = None
    target_filters: Optional[Dict[str, TargetFilter]] = None
    tags: Optional[TagInfo] = None
    description: Optional[str] = Field(default=None, max_length=1000)


class TagRulePreview(BaseModel):
    """Request model for previewing tag rule matches"""
    target_filters: Dict[str, TargetFilter] = Field(description="Target filters to preview")
    count_only: bool = Field(default=True, description="Return only count or full results")


class TagRuleExecute(BaseModel):
    """Request model for executing a tag rule"""
    force: bool = Field(default=False, description="Force re-execution even if recently run")


# =============================================================================
# Asset Models (3 endpoints)
# =============================================================================

class AssetFilter(BaseModel):
    """A single structured filter for asset queries. `field_id` is a typed
    FieldId (discriminated union); `op` is a string operator; `value` is the
    filter value (omitted for unary operators like empty/notempty)."""
    model_config = ConfigDict(extra="forbid")
    field_id: FieldId = Field(description="Typed FieldId (discriminator: kind)")
    op: str = Field(description="Operator: eq, neq, gt, gte, lt, lte, contains, ncontains, starts, ends, empty, notempty, any, all, none")
    value: Optional[Any] = Field(default=None, description="Filter value (not required for empty/notempty)")

    @field_validator('op')
    @classmethod
    def validate_operator(cls, v):
        from src.modules.fields.operators import OPERATOR_REGISTRY
        valid_ops = set(OPERATOR_REGISTRY)  # canonical codes incl. all/none
        if v not in valid_ops:
            raise ValueError(
                f'Invalid operator: {v}. Must be one of: {", ".join(sorted(valid_ops))}'
            )
        return v


class AssetListParams(BaseModel):
    """Query parameters for listing assets.

    `sort_by` and each entry in `fields` are encoded FieldId strings
    (e.g. "static:Hostname", "ext:Intune%20Full:deviceType"). `filters` is a
    JSON array of AssetFilter objects with typed field_id.
    """
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=10000)
    os_type: Optional[str] = None
    asset_type: Optional[str] = None
    tag: Optional[str] = None
    provider: Optional[str] = None
    search: Optional[str] = Field(default=None, description="Search by hostname, IP, MAC, or serial number")
    alert_id: Optional[str] = Field(
        default=None,
        description="Alert UUID — narrow to the assets that alert names. Not a "
                    "field predicate: no catalog entry holds it, so it is resolved "
                    "server-side against alerts.asset_ids rather than via `filters`."
    )
    sort_by: Optional[str] = Field(default=None, description="Encoded FieldId to sort by")
    sort_dir: Optional[str] = Field(default=None, description="Sort direction: asc or desc")
    filters: Optional[str] = Field(
        default=None,
        description="JSON array of AssetFilter objects (typed field_id + op + value)"
    )
    fields: Optional[str] = Field(
        default=None,
        description="CSV of encoded FieldId strings (e.g. 'static:Hostname,ext:Intune%20Full:deviceType'). "
                    "When provided, returns raw data with dynamic columns for Explorer."
    )

    @validator('sort_dir')
    def validate_sort_dir(cls, v):
        if v is not None and v not in ('asc', 'desc'):
            raise ValueError('sort_dir must be "asc" or "desc"')
        return v

    @field_validator('sort_by')
    @classmethod
    def validate_sort_by(cls, v):
        if v is None or v == '':
            return v
        decode_field_id(v)  # raises ValueError on malformed → 422
        return v

    @field_validator('fields')
    @classmethod
    def validate_fields(cls, v):
        if v is None or v == '':
            return v
        for s in v.split(","):
            s = s.strip()
            if s:
                decode_field_id(s)
        return v


class AssetSearchParams(BaseModel):
    """Query parameters for asset search"""
    q: str = Field(min_length=1, description="Search query")
    field: Optional[str] = Field(default=None, description="Encoded FieldId to narrow search (currently ignored)")
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator('field')
    @classmethod
    def validate_field(cls, v):
        if v is None or v == '':
            return v
        decode_field_id(v)
        return v


class AssetValuesParams(BaseModel):
    """Query parameters for getting distinct field values"""
    field: str = Field(description="Encoded FieldId to get values for")
    limit: int = Field(default=50, ge=1, le=500)
    search: Optional[str] = Field(default=None, description="Filter values by prefix match")
    filters: Optional[str] = Field(
        default=None,
        description="JSON array of AssetFilter objects (typed field_id + op + value)"
    )
    include_total: bool = Field(
        default=True,
        description="Include the total distinct count (a second query). "
                    "Pass false to skip it — total_distinct comes back null."
    )

    @field_validator('field')
    @classmethod
    def validate_field(cls, v):
        decode_field_id(v)
        return v


class ExplorerFieldsParams(BaseModel):
    """Query parameters for the Explorer field catalog."""
    include_filter_only: bool = Field(
        default=False,
        description="Include filter-only fields — filterable, but never "
                    "projectable, aggregatable or sortable. Off by default so "
                    "column pickers, pivot pickers and chart wizards cannot "
                    "offer a field that would hang on those paths."
    )


class AssetAggregateParams(BaseModel):
    """Query parameters for aggregating assets"""
    row_field: str = Field(description="Encoded FieldId to group rows by")
    col_field: Optional[str] = Field(default=None, description="Encoded FieldId to group columns by (optional)")
    filters: Optional[str] = Field(
        default=None,
        description="JSON array of AssetFilter objects (typed field_id + op + value)"
    )
    alert_id: Optional[str] = Field(
        default=None,
        description="Alert UUID — narrow the aggregate to the assets that alert "
                    "names. Not a field predicate: resolved server-side against "
                    "alerts.asset_ids rather than via `filters` (#1013)."
    )

    @field_validator('row_field', 'col_field')
    @classmethod
    def validate_encoded_fields(cls, v):
        if v is None or v == '':
            return v
        decode_field_id(v)
        return v


# =============================================================================
# Dashboard Models (7 endpoints)
# =============================================================================

class DashboardTimeRange(str, Enum):
    """Time range options for dashboard"""
    day = "24h"
    week = "7d"
    month = "30d"
    quarter = "90d"
    year = "365d"


class DashboardSummaryParams(BaseModel):
    """Query parameters for dashboard summary"""
    refresh: bool = Field(default=False, description="Force refresh cached data")


class AssetGrowthParams(BaseModel):
    """Query parameters for asset growth"""
    range: DashboardTimeRange = Field(default=DashboardTimeRange.month)
    interval: Optional[str] = Field(default="day", description="Grouping interval (hour, day, week, month)")


# =============================================================================
# Integration Models (38 endpoints)
# =============================================================================

class IntegrationType(str, Enum):
    """Integration connector types"""
    jamf = "jamf"
    active_directory = "active_directory"
    azure_ad = "azure_ad"
    intune = "intune"
    servicenow_cmdb = "servicenow_cmdb"
    splunk = "splunk"
    sentinelone = "sentinelone"
    sentinelone_siem = "sentinelone_siem"
    log_analytics = "log_analytics"
    manual_upload = "manual_upload"


class IntegrationCreate(BaseModel):
    """Request model for creating an integration"""
    name: str = Field(min_length=1, max_length=200, description="Integration name")
    connector_type: IntegrationType = Field(description="Connector type")
    enabled: bool = Field(default=True, description="Whether integration is enabled")
    config: Dict[str, Any] = Field(description="Connector-specific configuration")
    description: Optional[str] = Field(default=None, max_length=1000)

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Jamf Pro - Production",
                "connector_type": "jamf",
                "enabled": True,
                "config": {
                    "url": "https://company.jamfcloud.com",
                    "username": "api_user",
                    "password": "api_password",
                    "verify_ssl": True
                },
                "description": "Production Jamf Pro instance"
            }
        }


class IntegrationUpdate(BaseModel):
    """Request model for updating an integration"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    enabled: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None
    description: Optional[str] = Field(default=None, max_length=1000)


class IntegrationTestRequest(BaseModel):
    """Request model for testing integration connection"""
    config: Optional[Dict[str, Any]] = Field(default=None, description="Config to test (or use existing)")


class IntegrationListParams(BaseModel):
    """Query parameters for listing integrations"""
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=200)
    connector_type: Optional[IntegrationType] = None
    enabled: Optional[bool] = None


# Schedule Models

class ScheduleUpdate(BaseModel):
    """Request model for updating integration schedule"""
    enabled: bool = Field(description="Whether schedule is enabled")
    interval_minutes: Optional[int] = Field(default=None, ge=5, le=10080, description="Interval in minutes (5 min to 7 days)")
    cron_expression: Optional[str] = Field(default=None, description="Cron expression (alternative to interval)")

    @validator('cron_expression')
    def validate_cron(cls, v):
        """Validate cron expression format"""
        if v and not v.strip():
            return None
        # Basic validation - cron should have 5-6 fields
        if v and len(v.split()) not in [5, 6]:
            raise ValueError("Invalid cron expression format")
        return v


class ScheduleTriggerRequest(BaseModel):
    """Request model for triggering schedule"""
    force: bool = Field(default=False, description="Force trigger even if schedule is disabled")


# Batch Models

class BatchMapRequest(BaseModel):
    """Request model for mapping batch records"""
    batch_id: str = Field(description="Batch ID to map")
    force_remap: bool = Field(default=False, description="Force remapping even if already mapped")


class BatchRemapRequest(BaseModel):
    """Request model for remapping specific records"""
    record_ids: List[str] = Field(description="Record IDs to remap")


class BatchListParams(BaseModel):
    """Query parameters for listing batches"""
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=200)
    integration_id: Optional[str] = None
    status: Optional[str] = Field(default=None, description="Filter by status (running, completed, failed)")


# Asset Generation Models

class GenerationTriggerRequest(BaseModel):
    """Request model for triggering asset generation"""
    batch_id: Optional[str] = Field(default=None, description="Specific batch ID (or all batches if not provided)")
    force: bool = Field(default=False, description="Force generation even if recently run")
    dry_run: bool = Field(default=False, description="Preview changes without applying")


class GenerationHistoryParams(BaseModel):
    """Query parameters for generation history"""
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=200)
    integration_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


# =============================================================================
# Reports Models
# =============================================================================

class ReportCreateRequest(BaseModel):
    """Request model for creating a new report"""
    title: str = Field(..., min_length=1, max_length=255, description="Report title")
    description: Optional[str] = Field(default=None, description="Report description")
    charts: List[Dict[str, Any]] = Field(..., description="Array of chart configurations")

    @validator('charts')
    def validate_charts(cls, v):
        """Validate charts array"""
        if not isinstance(v, list):
            raise ValueError('charts must be an array')
        # Allow empty charts array (reports can be empty)
        # Validate each chart has required fields
        for idx, chart in enumerate(v):
            if not isinstance(chart, dict):
                raise ValueError(f'Chart at index {idx} must be an object')
            if 'chartId' not in chart:
                raise ValueError(f'Chart at index {idx} missing required field: chartId')
            if 'name' not in chart:
                raise ValueError(f'Chart at index {idx} missing required field: name')
        return v


class ReportUpdateRequest(BaseModel):
    """Request model for updating an existing report"""
    title: Optional[str] = Field(default=None, min_length=1, max_length=255, description="Report title")
    description: Optional[str] = Field(default=None, description="Report description")
    charts: Optional[List[Dict[str, Any]]] = Field(default=None, description="Array of chart configurations")

    @validator('charts')
    def validate_charts(cls, v):
        """Validate charts array if provided"""
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError('charts must be an array')
        # Allow empty charts array (reports can be empty)
        # Validate each chart has required fields
        for idx, chart in enumerate(v):
            if not isinstance(chart, dict):
                raise ValueError(f'Chart at index {idx} must be an object')
            if 'chartId' not in chart:
                raise ValueError(f'Chart at index {idx} missing required field: chartId')
            if 'name' not in chart:
                raise ValueError(f'Chart at index {idx} missing required field: name')
        return v


class DashboardUpdateRequest(BaseModel):
    """Request model for updating system-wide working dashboard"""
    charts: List[Dict[str, Any]] = Field(..., description="Array of chart configurations")
    expected_last_modified: Optional[datetime] = Field(
        default=None,
        description="Expected last_modified timestamp for conflict detection (optimistic locking)"
    )

    @validator('charts')
    def validate_charts(cls, v):
        """Validate charts array"""
        if not isinstance(v, list):
            raise ValueError('charts must be an array')
        # Empty array is allowed (user cleared dashboard)
        return v


class ReportListParams(PaginationParams):
    """Query parameters for listing reports"""
    search: Optional[str] = Field(default=None, description="Search in title and description")


# =============================================================================
# Health Models (1 endpoint)
# =============================================================================

# Health endpoint has no request models (GET only, no params)
