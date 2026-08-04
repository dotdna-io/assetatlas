# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pydantic models for the Enrichment CSV upload system.

These models define the data structures for enrichment dataset upload,
preview, status management, and API responses.
"""

import re
from datetime import datetime
from typing import Dict, Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.modules.enrichment.domain.match_field import (
    match_field_to_field_id,
    is_allowed_match_kind,
    is_ip_field,
)


# =============================================================================
# Request Models
# =============================================================================


class EnrichmentDatasetCreate(BaseModel):
    """Request model for creating/uploading an enrichment dataset."""

    name: str = Field(
        ..., min_length=1, max_length=255, description="Dataset display name"
    )
    match_field: str = Field(
        ..., description="Asset field to match against CSV key column"
    )
    match_type: Literal["exact", "cidr"] = Field(
        "exact", description="Match strategy: exact string or CIDR subnet"
    )
    target_field: str = Field(
        ..., min_length=1, max_length=100,
        description="Name of the enrichment column to write into assets"
    )

    @field_validator("match_field")
    @classmethod
    def validate_match_field(cls, v: str) -> str:
        # Accept any core or data-source field, expressed as a canonical encoded
        # field id (static:/core:/ext:/group:...) or a legacy form (bare core
        # column, or ext.<source>.<key>). Existence in the live catalog is
        # checked later by the service via FieldResolver; here we validate only
        # the format and that the kind is matchable.
        field_id = match_field_to_field_id(v)  # raises ValueError on bad format
        if not is_allowed_match_kind(field_id):
            raise ValueError(
                f"match_field kind '{getattr(field_id, 'kind', '?')}' is not "
                f"supported for enrichment; pick a core or data-source field"
            )
        return v

    @field_validator("target_field")
    @classmethod
    def sanitize_target_field(cls, v: str) -> str:
        """Lowercase, replace non-alphanumeric with underscore, collapse and strip."""
        v = v.lower()
        v = re.sub(r"[^a-z0-9]+", "_", v)
        v = re.sub(r"_+", "_", v)
        v = v.strip("_")
        if not v:
            raise ValueError("target_field must contain at least one alphanumeric character")
        return v

    @model_validator(mode="after")
    def cidr_requires_ip(self) -> "EnrichmentDatasetCreate":
        if self.match_type == "cidr" and not is_ip_field(
            match_field_to_field_id(self.match_field)
        ):
            raise ValueError(
                "CIDR match_type is only allowed when match_field is the IP Address field"
            )
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Location Mapping",
                "match_field": "hostname",
                "match_type": "exact",
                "target_field": "location",
            }
        }
    )


class EnrichmentDatasetStatusUpdate(BaseModel):
    """Request model for toggling dataset status."""

    status: Literal["active", "inactive"] = Field(
        ..., description="New dataset status"
    )


# =============================================================================
# Response Models
# =============================================================================


class EnrichmentDatasetResponse(BaseModel):
    """Response model for a single enrichment dataset."""

    id: str = Field(..., description="Dataset ID")
    name: str = Field(..., description="Dataset display name")
    match_field: str = Field(..., description="Asset field used for matching")
    match_type: str = Field(..., description="Match strategy (exact or cidr)")
    target_field: str = Field(..., description="Enrichment column name")
    status: str = Field(..., description="Dataset status (active/inactive)")
    row_count: int = Field(..., description="Number of rows in CSV")
    matched_count: Optional[int] = Field(
        None, description="Number of assets matched (after apply)"
    )
    uploaded_by: Optional[str] = Field(
        None, description="Username of uploader"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "ds_abc123",
                "name": "Location Mapping",
                "match_field": "hostname",
                "match_type": "exact",
                "target_field": "location",
                "status": "active",
                "row_count": 150,
                "matched_count": 120,
                "uploaded_by": "admin",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            }
        },
    )


class EnrichmentPreviewResponse(BaseModel):
    """Response model for dataset preview/apply results."""

    dataset_id: str = Field(..., description="Dataset ID")
    dataset_name: str = Field(..., description="Dataset display name")
    matched_count: int = Field(..., description="Number of assets matched")
    total_rows: int = Field(..., description="Total rows in CSV")
    sample_matches: List[Dict[str, Any]] = Field(
        ..., description="Sample matched rows for preview"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "dataset_id": "ds_abc123",
                "dataset_name": "Location Mapping",
                "matched_count": 42,
                "total_rows": 100,
                "sample_matches": [
                    {"hostname": "srv-01", "location": "NYC"},
                    {"hostname": "srv-02", "location": "LAX"},
                ],
            }
        }
    )
