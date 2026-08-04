# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Inventory Data Models

Pydantic models for asset API responses and requests.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class ConfidenceLevel(str, Enum):
    """Confidence level based on confidence score"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AssetTag(BaseModel):
    """Asset tag with color"""
    name: str = Field(..., description="Tag name")
    color: str = Field(..., description="Tag color (hex code)")
    type: str = Field(..., description="Tag type (pass/fail/classification)")


class AssetStatus(BaseModel):
    """Asset compliance and security status"""
    is_compliant: Optional[bool] = Field(None, description="Compliance status")
    is_secure: Optional[bool] = Field(None, description="Security status")
    alert_count: int = Field(0, description="Number of active alerts")
    in_grace: bool = Field(
        False,
        description="Asset is absent from all inputs but carried forward in the removal grace period (#795)",
    )
    days_in_grace: Optional[int] = Field(
        None,
        description="Whole days the asset has been continuously absent; None when present",
    )


class ProviderFieldSection(BaseModel):
    """Organized fields from a data provider"""
    general: Dict[str, Any] = Field(default_factory=dict, description="General information fields")
    network: Dict[str, Any] = Field(default_factory=dict, description="Network-related fields")
    additional: Dict[str, Any] = Field(default_factory=dict, description="Additional/extended fields")
    extended: Dict[str, Any] = Field(default_factory=dict, description="Extended fields from data_source_fields JSONB")


class AssetProvider(BaseModel):
    """Data source that contributed to this asset"""
    source_id: str = Field(..., description="Integration ID")
    source_name: Optional[str] = Field(None, description="Human-readable source name")
    source_icon: str = Field("📦", description="Emoji icon for the source")
    source_type: str = Field(..., description="Integration type (e.g., 'jamf', 'vcenter')")
    source_category: Optional[str] = Field(None, description="Integration category (e.g., 'EDR', 'SIEM')")
    status: str = Field(..., description="Source status (active/inactive)")
    last_sync: Optional[datetime] = Field(None, description="Last sync timestamp")
    field_count: int = Field(0, description="Total number of fields from this source")
    record_count: int = Field(1, description="Number of raw records from this source")
    fields: ProviderFieldSection = Field(..., description="Organized field data")


class AssetDetail(BaseModel):
    """Detailed asset information"""
    # Identity
    id: UUID = Field(..., description="Asset UUID")
    hostname: Optional[str] = Field(None, description="Primary hostname")
    hostnames: List[str] = Field(default_factory=list, description="All hostnames (including merged)")
    fqdn: Optional[str] = Field(None, description="Fully qualified domain name")

    # Classification
    category: Optional[str] = Field(None, description="Asset category")
    subcategory: Optional[str] = Field(None, description="Asset subcategory")
    vendor_category: Optional[str] = Field(None, description="Vendor-provided device category (classification hint)")

    # Technical details
    os: Optional[str] = Field(None, description="Operating system (normalized)")
    model: Optional[str] = Field(None, description="Hardware model")
    manufacturer: Optional[str] = Field(None, description="Manufacturer")

    # Network information (can be multiple)
    ip: List[str] = Field(default_factory=list, description="IP addresses")
    mac: List[str] = Field(default_factory=list, description="MAC addresses")
    mac_vendor: Optional[str] = Field(None, description="MAC vendor")
    sn: Optional[str] = Field(None, description="Serial number")

    # Confidence
    confidence: float = Field(0.0, description="Confidence score (0.0 - 1.0)")
    confidence_level: ConfidenceLevel = Field(..., description="High/Medium/Low")
    confidence_explanation: str = Field("", description="AI-generated explanation of confidence score")
    confidence_breakdown: Optional[Dict[str, Any]] = Field(None, description="Scoring sub-components: base_score, penalties, identifier type, missing fields")

    # Timestamps
    last_seen: Optional[datetime] = Field(None, description="Last time asset was seen")
    first_seen: Optional[datetime] = Field(None, description="First time asset was discovered")

    # Tags and status
    tags: List[AssetTag] = Field(default_factory=list, description="Asset tags with colors")
    status: AssetStatus = Field(default_factory=AssetStatus, description="Compliance and security status")

    # Additional metadata — per-source observations exposed as primary scalar + full array
    owner: Optional[str] = Field(None, description="Primary asset owner (first of owners)")
    owners: List[str] = Field(default_factory=list, description="All owners reported across sources")
    location: Optional[str] = Field(None, description="Primary location (first of locations)")
    locations: List[str] = Field(default_factory=list, description="All locations reported across sources")
    ipam_location: Optional[str] = Field(None, description="Primary IPAM-enriched location (first of ipam_locations)")
    ipam_locations: List[str] = Field(default_factory=list, description="All IPAM-enriched locations across sources")
    user: Optional[str] = Field(None, description="Primary user (first of users)")
    users: List[str] = Field(default_factory=list, description="All users reported across sources")

    # Extended attributes from all sources
    extended_attributes: Dict[str, Any] = Field(default_factory=dict, description="Extended attributes by source")


class OtherSource(BaseModel):
    """A data source that has assets in the same category but didn't contribute to this asset"""
    source_name: str = Field(..., description="Integration name")
    source_icon: str = Field("📦", description="Emoji or path icon")
    source_type: str = Field(..., description="Integration type")
    asset_count: int = Field(0, description="Number of assets this source has in the category")


class AssetDetailResponse(BaseModel):
    """API response for asset detail endpoint"""
    success: bool = Field(True, description="Request success status")
    asset: AssetDetail = Field(..., description="Asset details")
    providers: List[AssetProvider] = Field(default_factory=list, description="Data sources for this asset")
    other_sources: List[OtherSource] = Field(default_factory=list, description="Other sources with assets in this category")


class AssetListItem(BaseModel):
    """Lightweight asset info for list views"""
    id: UUID
    hostname: Optional[str]
    category: Optional[str]
    os: Optional[str]
    ip: List[str] = Field(default_factory=list)
    confidence: float
    confidence_level: Optional[str] = None
    last_seen: Optional[datetime]
    tags: List[AssetTag] = Field(default_factory=list)


class AssetListResponse(BaseModel):
    """API response for asset list endpoint"""
    success: bool = Field(True)
    assets: List[AssetListItem] = Field(default_factory=list)
    pagination: Dict[str, int] = Field(
        default_factory=lambda: {"total": 0, "page": 1, "limit": 50, "pages": 0}
    )


class AssetSearchResult(BaseModel):
    """Search result item"""
    id: UUID
    hostname: Optional[str]
    category: Optional[str]
    ip: List[str] = Field(default_factory=list)
    match_field: str = Field(..., description="Which field matched the search")


class AssetSearchResponse(BaseModel):
    """API response for asset search endpoint"""
    success: bool = Field(True)
    results: List[AssetSearchResult] = Field(default_factory=list)
    query: str = Field(..., description="Original search query")
