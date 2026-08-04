# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Lansweeper Connector

Collects IT asset inventory data from Lansweeper via the GraphQL API (v2).
Supports both Personal Access Token (PAT) and OAuth 2.0 authentication.

Endpoint: POST https://api.lansweeper.com/api/v2/graphql
Pagination: Cursor-based via assetPagination (FIRST page, then NEXT with cursor)
Rate limits: 60 req/min, burst 10, respect Retry-After on 429
Field limit: 30 fields per query
"""

from typing import Dict, List, Iterator, Any, Optional

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import (
    APIKeyAuth,
    OAuth2RefreshToken,
    CursorPaginator,
    ConfigSchema,
)


# Lansweeper GraphQL API endpoint
API_URL = "https://api.lansweeper.com/api/v2/graphql"

# OAuth token endpoint
TOKEN_URL = "https://api.lansweeper.com/api/integrations/oauth/token"

# Pagination limits
MAX_PAGE_SIZE = 500
MAX_PAGINATION_PAGES = 200  # Safety cap: 200 * 500 = 100K records per site

# Default fields to request (30 max per API limit)
DEFAULT_FIELDS = [
    "key",
    "url",
    "siteId",
    "assetBasicInfo.name",
    "assetBasicInfo.type",
    "assetBasicInfo.subType",
    "assetBasicInfo.ipAddress",
    "assetBasicInfo.mac",
    "assetBasicInfo.fqdn",
    "assetBasicInfo.domain",
    "assetBasicInfo.userName",
    "assetBasicInfo.userDomain",
    "assetBasicInfo.description",
    "assetBasicInfo.firstSeen",
    "assetBasicInfo.lastSeen",
    "assetBasicInfo.lastChanged",
    "assetBasicInfo.lastActiveScan",
    "operatingSystem.caption",
    "operatingSystem.version",
    "operatingSystem.buildNumber",
    "assetCustom.manufacturer",
    "assetCustom.model",
    "assetCustom.serialNumber",
    "assetCustom.location",
    "assetCustom.department",
    "assetCustom.contact",
    "assetCustom.stateName",
    "assetCustom.warrantyDate",
    "assetCustom.dnsName",
    "assetCustom.lastPatched",
]


@ConnectorRegistry.register
class LansweeperConnector(BaseConnector):
    """
    Lansweeper Connector

    Fetches asset inventory from Lansweeper via the GraphQL API v2.
    Supports PAT and OAuth 2.0 authentication. Multi-site collection.
    """

    # Connector Metadata
    integration_type = "lansweeper"
    display_name = "Lansweeper"
    category = "Network Discovery"
    description = "Collect asset inventory data from Lansweeper"
    logo_url = "/logos/lansweeper.png"

    def __init__(self, config: Dict[str, Any]):
        # Auth method: 'pat' or 'oauth2'
        self.auth_method = config.get("auth_method", "pat")

        # Collection settings
        self.site_id = config.get("site_id")  # None = all sites
        self.page_size = min(int(config.get("page_size", MAX_PAGE_SIZE)), MAX_PAGE_SIZE)
        self.fields = config.get("fields", DEFAULT_FIELDS[:])

        super().__init__(config)

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        if self.auth_method == "oauth2":
            return OAuth2RefreshToken({
                'token_url': TOKEN_URL,
                'client_id': self.config.get('client_id'),
                'client_secret': self.config.get('client_secret'),
                'refresh_token': self.config.get('refresh_token'),
                'base_url': API_URL,
            }, self.logger)
        else:
            # PAT mode — token sent as Authorization: Token <pat>
            return APIKeyAuth({
                'api_key': self.config.get('personal_access_token'),
                'api_key_header': 'Authorization',
                'api_key_prefix': 'Token ',
                'base_url': API_URL,
            }, self.logger)

    # ------------------------------------------------------------------
    # Config schema (class method -- no instance needed)
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_select(
                'auth_method', 'Authentication Method',
                options=[
                    {'value': 'pat', 'label': 'Personal Access Token (PAT)'},
                    {'value': 'oauth2', 'label': 'OAuth 2.0'},
                ],
                default='pat',
                required=True,
                description='Choose PAT (Personal Access Token) or OAuth 2.0',
            )
            .add_password(
                'personal_access_token', 'Personal Access Token',
                required=False,
                description='Lansweeper API personal access token',
            )
            .add_string(
                'client_id', 'Client ID',
                required=False,
                description='OAuth 2.0 application client ID',
            )
            .add_password(
                'client_secret', 'Client Secret',
                required=False,
                description='OAuth 2.0 application client secret',
            )
            .add_password(
                'refresh_token', 'Refresh Token',
                required=False,
                description='OAuth 2.0 refresh token',
            )
            .add_string(
                'site_id', 'Site ID',
                required=False,
                placeholder='Leave blank to collect from all authorized sites',
                description='Specific Lansweeper site ID to collect from (leave blank for all sites)',
            )
            .add_number(
                'page_size', 'Page Size',
                default=500,
                min_val=1,
                max_val=500,
                description='Number of asset records per API request (max 500)',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info("Testing connection to Lansweeper API...")

            sites = self._get_authorized_sites()

            site_names = [s.get("name", s.get("id", "unknown")) for s in sites]

            return {
                "success": True,
                "message": f"Successfully connected to Lansweeper. Found {len(sites)} authorized site(s).",
                "metadata": {
                    "site_count": len(sites),
                    "sites": site_names,
                    "auth_method": self.auth_method,
                    "api_endpoint": API_URL,
                },
            }

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return {
                "success": False,
                "message": f"Connection failed: {str(e)}",
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # _collect_records (replaces fetch_data)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        # Determine which sites to collect from
        if self.site_id:
            site_ids = [self.site_id]
        else:
            sites = self._get_authorized_sites()
            site_ids = [s["id"] for s in sites]

        self.logger.info(f"Collecting assets from {len(site_ids)} site(s)")

        for site_id in site_ids:
            site_record_count = 0

            paginator = CursorPaginator(max_pages=MAX_PAGINATION_PAGES)

            for page in paginator.pages(lambda cursor, sid=site_id: self._fetch_site_page(sid, cursor)):
                for raw_record in page:
                    site_record_count += 1
                    yield self._flatten_asset_record(raw_record)

            self.logger.info(
                f"Site {site_id}: fetched {site_record_count:,} assets"
            )

    # ------------------------------------------------------------------
    # Internal: site discovery
    # ------------------------------------------------------------------

    def _get_authorized_sites(self) -> list:
        """Fetch authorized sites from the Lansweeper API."""
        query_data = self._build_sites_query()
        data = self._execute_graphql(query_data)

        sites = data.get("data", {}).get("authorizedSites", {}).get("sites", [])
        self.logger.info(f"Found {len(sites)} authorized site(s)")
        return sites

    # ------------------------------------------------------------------
    # Internal: page fetcher for CursorPaginator
    # ------------------------------------------------------------------

    def _fetch_site_page(self, site_id: str, cursor):
        """
        Fetch one page of assets from a site.

        Args:
            site_id: Lansweeper site ID
            cursor: Pagination cursor, or None for the first page.

        Returns:
            (records, next_cursor) tuple for CursorPaginator.
        """
        query_data = self._build_assets_query(site_id, cursor, self.page_size, self.fields)
        data = self._execute_graphql(query_data)

        asset_resources = (
            data.get("data", {}).get("site", {}).get("assetResources", {})
        )
        items = asset_resources.get("items", [])

        # Check for next page
        pagination = asset_resources.get("pagination", {})
        next_cursor = pagination.get("next")

        return items, next_cursor

    # ------------------------------------------------------------------
    # Internal: GraphQL execution
    # ------------------------------------------------------------------

    def _execute_graphql(self, query_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a GraphQL query via the auth strategy."""
        response = self.auth.request(
            API_URL,
            method='POST',
            json=query_data,
        )

        data = response.json()

        # Check for GraphQL errors (HTTP 200 can contain errors)
        if "errors" in data:
            error_messages = [
                e.get("message", str(e)) for e in data["errors"]
            ]
            error_str = "; ".join(error_messages)
            raise RuntimeError(f"GraphQL errors: {error_str}")

        return data

    # ------------------------------------------------------------------
    # Internal: GraphQL query builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_sites_query() -> Dict[str, Any]:
        """Build the GraphQL query to fetch authorized sites."""
        return {
            "query": """
            query {
                authorizedSites {
                    sites {
                        id
                        name
                    }
                }
            }
            """
        }

    @staticmethod
    def _build_assets_query(
        site_id: str,
        cursor: Optional[str],
        page_size: int,
        fields: list,
    ) -> Dict[str, Any]:
        """Build the GraphQL query to fetch asset resources."""
        if cursor is None:
            query = """
            query GetSiteAssets(
                $siteId: ID!,
                $pageSize: Int!,
                $requestedFields: [String!]!
            ) {
                site(id: $siteId) {
                    assetResources(
                        assetPagination: { limit: $pageSize, page: FIRST },
                        fields: $requestedFields
                    ) {
                        total
                        items
                        pagination { next }
                    }
                }
            }
            """
            variables = {
                "siteId": site_id,
                "pageSize": page_size,
                "requestedFields": fields,
            }
        else:
            query = """
            query GetSiteAssets(
                $siteId: ID!,
                $pageSize: Int!,
                $cursor: ID!,
                $requestedFields: [String!]!
            ) {
                site(id: $siteId) {
                    assetResources(
                        assetPagination: { limit: $pageSize, page: NEXT, cursor: $cursor },
                        fields: $requestedFields
                    ) {
                        total
                        items
                        pagination { next }
                    }
                }
            }
            """
            variables = {
                "siteId": site_id,
                "pageSize": page_size,
                "cursor": cursor,
                "requestedFields": fields,
            }

        return {"query": query, "variables": variables}

    # ------------------------------------------------------------------
    # Internal: record flattening
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten_asset_record(raw_item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Flatten a nested Lansweeper asset record into a flat dictionary.

        Nested objects like assetBasicInfo.name become assetBasicInfo_name.
        """
        record = {}

        for key, value in raw_item.items():
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    record[f"{key}_{nested_key}"] = nested_value
            else:
                record[key] = value

        return record

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        fields = [
            {"name": "key", "path": "key", "type": "string", "description": "Unique asset key", "required": True},
            {"name": "url", "path": "url", "type": "string", "description": "Asset URL in Lansweeper"},
            {"name": "siteId", "path": "siteId", "type": "string", "description": "Lansweeper site ID"},
            {"name": "assetBasicInfo_name", "path": "assetBasicInfo_name", "type": "string", "description": "Asset name"},
            {"name": "assetBasicInfo_type", "path": "assetBasicInfo_type", "type": "string", "description": "Asset type"},
            {"name": "assetBasicInfo_subType", "path": "assetBasicInfo_subType", "type": "string", "description": "Asset sub-type"},
            {"name": "assetBasicInfo_ipAddress", "path": "assetBasicInfo_ipAddress", "type": "string", "description": "IP address"},
            {"name": "assetBasicInfo_mac", "path": "assetBasicInfo_mac", "type": "string", "description": "MAC address"},
            {"name": "assetBasicInfo_fqdn", "path": "assetBasicInfo_fqdn", "type": "string", "description": "Fully qualified domain name"},
            {"name": "assetBasicInfo_domain", "path": "assetBasicInfo_domain", "type": "string", "description": "Domain name"},
            {"name": "assetBasicInfo_userName", "path": "assetBasicInfo_userName", "type": "string", "description": "Last logged-in user"},
            {"name": "assetBasicInfo_userDomain", "path": "assetBasicInfo_userDomain", "type": "string", "description": "User domain"},
            {"name": "assetBasicInfo_description", "path": "assetBasicInfo_description", "type": "string", "description": "Asset description"},
            {"name": "assetBasicInfo_firstSeen", "path": "assetBasicInfo_firstSeen", "type": "datetime", "description": "First seen timestamp"},
            {"name": "assetBasicInfo_lastSeen", "path": "assetBasicInfo_lastSeen", "type": "datetime", "description": "Last seen timestamp"},
            {"name": "assetBasicInfo_lastChanged", "path": "assetBasicInfo_lastChanged", "type": "datetime", "description": "Last changed timestamp"},
            {"name": "assetBasicInfo_lastActiveScan", "path": "assetBasicInfo_lastActiveScan", "type": "datetime", "description": "Last active scan timestamp"},
            {"name": "operatingSystem_caption", "path": "operatingSystem_caption", "type": "string", "description": "Operating system name"},
            {"name": "operatingSystem_version", "path": "operatingSystem_version", "type": "string", "description": "OS version"},
            {"name": "operatingSystem_buildNumber", "path": "operatingSystem_buildNumber", "type": "string", "description": "OS build number"},
            {"name": "assetCustom_manufacturer", "path": "assetCustom_manufacturer", "type": "string", "description": "Hardware manufacturer"},
            {"name": "assetCustom_model", "path": "assetCustom_model", "type": "string", "description": "Hardware model"},
            {"name": "assetCustom_serialNumber", "path": "assetCustom_serialNumber", "type": "string", "description": "Serial number"},
            {"name": "assetCustom_location", "path": "assetCustom_location", "type": "string", "description": "Asset location"},
            {"name": "assetCustom_department", "path": "assetCustom_department", "type": "string", "description": "Department"},
            {"name": "assetCustom_contact", "path": "assetCustom_contact", "type": "string", "description": "Contact person"},
            {"name": "assetCustom_stateName", "path": "assetCustom_stateName", "type": "string", "description": "Asset state"},
            {"name": "assetCustom_warrantyDate", "path": "assetCustom_warrantyDate", "type": "datetime", "description": "Warranty expiry date"},
            {"name": "assetCustom_dnsName", "path": "assetCustom_dnsName", "type": "string", "description": "DNS name"},
            {"name": "assetCustom_lastPatched", "path": "assetCustom_lastPatched", "type": "datetime", "description": "Last patched date"},
        ]
        return {
            "source": self.integration_type,
            "version": "1.0",
            "fields": fields,
            "sample_record_count": 3,
        }
