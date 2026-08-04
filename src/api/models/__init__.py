# SPDX-License-Identifier: AGPL-3.0-or-later
"""
API Pydantic Models

Request and response models for all API endpoints, organized by domain.

Usage:
    from src.api.models.requests import TagRuleCreate, AssetListParams
    from src.api.models.responses import AssetDetail, DashboardSummary
"""

# Import commonly used models for convenience
from .requests import (
    # Tag Rules
    TagRuleCreate,
    TagRuleUpdate,
    TagRulePreview,

    # Assets
    AssetListParams,
    AssetSearchParams,

    # Common
    PaginationParams,
)

from .responses import (
    # Common
    SuccessResponse,
    ErrorResponse,
    PaginatedResponse,

    # Assets
    AssetDetail,
    AssetList,

    # Dashboard
    DashboardSummary,
)

__all__ = [
    # Request models
    'TagRuleCreate',
    'TagRuleUpdate',
    'TagRulePreview',
    'AssetListParams',
    'AssetSearchParams',
    'PaginationParams',

    # Response models
    'SuccessResponse',
    'ErrorResponse',
    'PaginatedResponse',
    'AssetDetail',
    'AssetList',
    'DashboardSummary',
]
