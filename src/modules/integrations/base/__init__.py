# SPDX-License-Identifier: AGPL-3.0-or-later
"""Base connector framework components"""

from src.modules.integrations.base.base_connector import BaseConnector
from src.modules.integrations.base.auth_strategies import (
    AuthStrategy,
    OAuth2ClientCredentials,
    OAuth2RefreshToken,
    OAuth2TokenExchange,
    LoginPasswordJsonAuth,
    BasicAuth,
    APIKeyAuth,
    NoAuth,
    LDAPAuth,
    get_auth_strategy,
)
from src.modules.integrations.base.rate_limiter import RateLimiter, AdaptiveRateLimiter, HeaderRateLimiter
from src.modules.integrations.base.pagination import (
    NoPaginator, OffsetPaginator, PagePaginator, CursorPaginator, LinkPaginator,
)
from src.modules.integrations.base.config_schema import ConfigSchema

__all__ = [
    'BaseConnector',
    'AuthStrategy',
    'OAuth2ClientCredentials', 'OAuth2RefreshToken', 'OAuth2TokenExchange',
    'LoginPasswordJsonAuth',
    'BasicAuth', 'APIKeyAuth', 'NoAuth', 'LDAPAuth',
    'get_auth_strategy',
    'RateLimiter', 'AdaptiveRateLimiter', 'HeaderRateLimiter',
    'NoPaginator', 'OffsetPaginator', 'PagePaginator', 'CursorPaginator', 'LinkPaginator',
    'ConfigSchema',
]
