# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Response Pydantic Models

Response models for all API endpoints, providing type-safe response structures.

Models are organized by domain:
- Common responses
- Tag Rules
- Assets
- Dashboard
- Integrations
- Health
"""

from typing import Optional, List, Dict, Any, Union
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


# =============================================================================
# Common Response Models
# =============================================================================

class SuccessResponse(BaseModel):
    """Standard success response wrapper"""
    success: bool = True
    data: Any
    meta: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Standard error response"""
    success: bool = False
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None


class PaginationMeta(BaseModel):
    """Pagination metadata"""
    page: int
    limit: int
    total: int
    pages: int
    has_next: bool
    has_prev: bool

    @classmethod
    def from_params(cls, page: int, limit: int, total: int) -> 'PaginationMeta':
        """
        Create pagination metadata from parameters.

        Args:
            page: Current page number
            limit: Items per page
            total: Total number of items

        Returns:
            PaginationMeta instance
        """
        import math
        pages = math.ceil(total / limit) if limit > 0 else 0
        return cls(
            page=page,
            limit=limit,
            total=total,
            pages=pages,
            has_next=page < pages,
            has_prev=page > 1
        )


class PaginatedResponse(BaseModel):
    """Paginated response wrapper"""
    success: bool = True
    data: Any
    meta: Dict[str, Any]  # Contains 'pagination' key


# =============================================================================
# Tag Rules Response Models
# =============================================================================

class TagRuleDetail(BaseModel):
    """Tag rule detail response"""
    id: str
    name: str
    type: str  # classification or assignment
    enabled: bool
    target_filters: Dict[str, Any]
    tags: Dict[str, str]  # {label, color}
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: str
    last_execution: Optional[datetime] = None
    execution_count: Optional[int] = 0
    last_match_count: Optional[int] = 0


class TagRuleList(BaseModel):
    """Tag rules list response"""
    rules: List[TagRuleDetail]
    total: int


class RuleExecutionResult(BaseModel):
    """Result of rule execution"""
    rule_id: str
    rule_name: str
    execution_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str  # running, completed, failed
    assets_matched: int
    tags_applied: int
    errors: List[str] = []


class RulePreviewResult(BaseModel):
    """Result of rule preview"""
    match_count: int
    matched_assets: Optional[List[Dict[str, Any]]] = None  # Only if count_only=False
    filters_applied: Dict[str, Any]


class FieldDefinition(BaseModel):
    """Field definition for rule creation"""
    name: str
    label: str
    type: str  # string, number, boolean, date, array
    operators: List[str]
    description: Optional[str] = None
    example: Optional[Any] = None


class FieldsResponse(BaseModel):
    """Available fields for tag rules"""
    target_filter_fields: List[FieldDefinition]
    compliance_criteria_fields: Optional[List[FieldDefinition]] = None
    advanced_fields: Optional[List[FieldDefinition]] = None
    extended_attributes_fields: Optional[List[FieldDefinition]] = None


class OperatorsResponse(BaseModel):
    """Available operators for tag rules"""
    operators: List[Dict[str, str]]  # [{name, label, description}]


class ExecutionHistoryEntry(BaseModel):
    """Execution history entry"""
    id: str
    rule_id: str
    rule_name: str
    executed_at: datetime
    status: str
    assets_matched: int
    duration_ms: Optional[int] = None


# =============================================================================
# Asset Response Models
# =============================================================================

class ProviderInfo(BaseModel):
    """Provider information for an asset"""
    integration_id: str
    integration_name: str
    connector_type: str
    last_seen: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    fields_provided: List[str]
    raw_data: Optional[Dict[str, Any]] = None


class AssetDetail(BaseModel):
    """Detailed asset information"""
    id: int
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    serial_number: Optional[str] = None

    # Classification
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    os_build: Optional[str] = None
    asset_type: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None

    # Metadata
    confidence_score: float = Field(ge=0.0, le=1.0)
    last_seen: datetime
    first_seen: datetime
    created_at: datetime
    updated_at: datetime

    # Enrichment
    mac_vendor: Optional[str] = None
    eol_date: Optional[datetime] = None
    eol_status: Optional[str] = None

    # Tags
    tags: List[str] = []

    # Providers
    providers: List[ProviderInfo] = []

    # Extended attributes
    extended_attributes: Optional[Dict[str, Any]] = None


class AssetSummary(BaseModel):
    """Summary asset information for lists"""
    id: int
    hostname: Optional[str]
    ip_address: Optional[str]
    mac_address: Optional[str]
    serial_number: Optional[str]
    os_type: Optional[str]
    asset_type: Optional[str]
    confidence_score: float
    last_seen: datetime
    tags: List[str] = []
    provider_count: int


class AssetList(BaseModel):
    """Asset list response"""
    assets: List[AssetSummary]
    total: int


class AssetSearchResult(BaseModel):
    """Asset search result"""
    assets: List[AssetSummary]
    query: str
    match_count: int


# =============================================================================
# Dashboard Response Models
# =============================================================================

class DashboardSummary(BaseModel):
    """Dashboard summary statistics"""
    total_assets: int
    assets_by_type: Dict[str, int]  # {workstation: 150, server: 45, ...}
    assets_by_os: Dict[str, int]  # {Windows: 120, macOS: 50, ...}
    active_integrations: int
    total_integrations: int
    recent_batches_count: int
    last_generation_run: Optional[datetime] = None
    tag_rules_count: int
    active_tag_rules: int


class AssetCountByType(BaseModel):
    """Asset counts by type"""
    workstation: int = 0
    server: int = 0
    mobile: int = 0
    network_device: int = 0
    printer: int = 0
    other: int = 0
    total: int


class IntegrationStatusSummary(BaseModel):
    """Integration health summary"""
    id: str
    name: str
    connector_type: str
    enabled: bool
    status: str  # healthy, warning, error
    last_run: Optional[datetime] = None
    last_success: Optional[datetime] = None
    consecutive_failures: int
    next_run: Optional[datetime] = None


class BatchSummary(BaseModel):
    """Recent batch summary"""
    id: str
    integration_id: str
    integration_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str  # running, completed, failed
    records_count: int
    mapped_count: int


class AssetGrowthPoint(BaseModel):
    """Asset growth data point"""
    timestamp: datetime
    total_assets: int
    new_assets: int
    updated_assets: int


class AssetGrowthResponse(BaseModel):
    """Asset growth over time"""
    data_points: List[AssetGrowthPoint]
    time_range: str
    interval: str


# =============================================================================
# Integration Response Models
# =============================================================================

class ConnectorSchema(BaseModel):
    """Connector configuration schema"""
    connector_type: str
    name: str
    description: str
    config_schema: Dict[str, Any]  # JSON Schema for config fields
    required_fields: List[str]
    optional_fields: List[str]


class IntegrationDetail(BaseModel):
    """Detailed integration information"""
    id: str
    name: str
    connector_type: str
    enabled: bool
    config: Dict[str, Any]  # Sensitive fields masked
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_run: Optional[datetime] = None
    last_success: Optional[datetime] = None
    consecutive_failures: int

    # Schedule info
    schedule_enabled: bool
    schedule_interval: Optional[int] = None
    schedule_cron: Optional[str] = None
    next_run: Optional[datetime] = None

    # Statistics
    total_batches: int
    total_records: int
    total_assets_created: int


class IntegrationSummary(BaseModel):
    """Summary integration info for lists"""
    id: str
    name: str
    connector_type: str
    enabled: bool
    last_run: Optional[datetime] = None
    status: str  # healthy, warning, error, never_run
    schedule_enabled: bool
    next_run: Optional[datetime] = None


class IntegrationList(BaseModel):
    """Integration list response"""
    integrations: List[IntegrationSummary]
    total: int


class ConnectionTestResult(BaseModel):
    """Integration connection test result"""
    success: bool
    message: str
    details: Optional[Dict[str, Any]] = None
    connector_type: str
    tested_at: datetime


class IntegrationStats(BaseModel):
    """Integration statistics"""
    total: int
    enabled: int
    disabled: int
    by_connector_type: Dict[str, int]
    available_connectors: int
    healthy: int
    warning: int
    error: int


class BatchDetail(BaseModel):
    """Detailed batch information"""
    id: str
    integration_id: str
    integration_name: str
    connector_type: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str
    raw_records_count: int
    mapped_records_count: int
    failed_records_count: int
    assets_created_count: int
    assets_updated_count: int
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None


class BatchRecord(BaseModel):
    """Individual batch record"""
    id: str
    batch_id: str
    source_id: str
    created_at: datetime
    mapped: bool
    mapped_at: Optional[datetime] = None
    asset_id: Optional[int] = None
    error_message: Optional[str] = None
    data: Dict[str, Any]


class ScheduleInfo(BaseModel):
    """Integration schedule information"""
    integration_id: str
    integration_name: str
    enabled: bool
    interval_minutes: Optional[int] = None
    cron_expression: Optional[str] = None
    next_run: Optional[datetime] = None
    last_run: Optional[datetime] = None


class SchedulerStatus(BaseModel):
    """Scheduler service status"""
    running: bool
    job_count: int
    jobs: List[Dict[str, Any]]


class GenerationRun(BaseModel):
    """Asset generation run information"""
    id: str
    integration_id: Optional[str] = None
    batch_id: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str  # running, completed, failed
    assets_created: int
    assets_updated: int
    assets_unchanged: int
    conflicts_detected: int
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None


# =============================================================================
# Reports Response Models
# =============================================================================

class ReportSummary(BaseModel):
    """Report summary for list view (system-wide)"""
    id: str
    title: str
    description: Optional[str] = None
    chart_count: int
    charts: List[Dict[str, Any]] = []
    is_default: bool = False  # Indicates if this is the default report
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None  # UUID for audit
    updated_by: Optional[str] = None  # UUID for audit

    class Config:
        from_attributes = True  # Enable from_orm functionality


class ReportDetail(BaseModel):
    """Detailed report information (system-wide)"""
    id: str
    title: str
    description: Optional[str] = None
    charts: List[Dict[str, Any]]
    is_default: bool = False  # Indicates if this is the default report
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None  # UUID for audit
    updated_by: Optional[str] = None  # UUID for audit

    class Config:
        from_attributes = True


class ReportListResponse(BaseModel):
    """List of reports with pagination"""
    reports: List[ReportSummary]
    total: int
    page: int
    limit: int
    has_more: bool


class DashboardResponse(BaseModel):
    """System-wide working dashboard"""
    charts: List[Dict[str, Any]]
    last_modified: Optional[datetime] = None  # Can be None if never modified

    class Config:
        from_attributes = True


# =============================================================================
# Health Response Models
# =============================================================================

class HealthCheck(BaseModel):
    """Health check response"""
    status: str  # healthy, degraded, unhealthy
    version: str
    timestamp: datetime
    database: str  # connected, disconnected
    services: Dict[str, str]  # {service_name: status}
    uptime_seconds: Optional[float] = None
