# SPDX-License-Identifier: AGPL-3.0-or-later
"""AssetAtlas MCP Server.

Exposes asset data, pipeline status, and monitoring tools to AI assistants
via Model Context Protocol (stdio for Claude Code, Streamable HTTP for production).

Usage:
    python -m src.mcp.server              # stdio (default, for Claude Code)
    python -m src.mcp.server --http       # Streamable HTTP on port 8100
    MCP_TRANSPORT=http python -m src.mcp.server
"""

import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from src.mcp.context import app_context_process_scope, app_lifespan

logger = logging.getLogger(__name__)


def _register_all(server: FastMCP) -> None:
    """Register all tools, resources, and prompts on the given server instance."""
    from src.mcp.tools.alert_rules import register_tools as reg_alert_rules_tools
    from src.mcp.tools.alerts import register_tools as reg_alert_tools
    from src.mcp.tools.assets import register_tools as reg_asset_tools
    from src.mcp.tools.audit import register_tools as reg_audit_tools
    from src.mcp.tools.categories import register_tools as reg_category_tools
    from src.mcp.tools.chronicle_queries import (
        register_tools as reg_chronicle_tools,
    )
    from src.mcp.tools.enrichment import register_tools as reg_enrichment_tools
    from src.mcp.tools.eol import register_tools as reg_eol_tools
    from src.mcp.tools.field_registry import register_tools as reg_field_registry_tools
    from src.mcp.tools.integration_diagnostics import (
        register_tools as reg_integration_diagnostics_tools,
    )
    from src.mcp.tools.monitoring import register_tools as reg_monitoring_tools
    from src.mcp.tools.pipeline_runs import register_tools as reg_pipeline_runs_tools
    from src.mcp.tools.posture import register_tools as reg_posture_tools
    from src.mcp.tools.reliability import register_tools as reg_reliability_tools
    from src.mcp.tools.reports import register_tools as reg_reports_tools
    from src.mcp.tools.scheduler import register_tools as reg_scheduler_tools
    from src.mcp.tools.settings_tool import register_tools as reg_settings_tools
    from src.mcp.tools.sources import register_tools as reg_sources_tools
    from src.mcp.tools.suggestions import register_tools as reg_suggestion_tools
    from src.mcp.tools.unified_fields import (
        register_tools as reg_unified_fields_tools,
    )
    from src.mcp.resources.schema import register_resources as reg_schema
    from src.mcp.resources.config import register_resources as reg_config
    from src.mcp.resources.stats import register_resources as reg_stats
    from src.mcp.resources.docs import register_resources as reg_docs
    from src.mcp.prompts.workflows import register_prompts as reg_prompts

    reg_alert_rules_tools(server)
    reg_alert_tools(server)
    reg_asset_tools(server)
    reg_audit_tools(server)
    reg_category_tools(server)
    reg_chronicle_tools(server)
    reg_enrichment_tools(server)
    reg_eol_tools(server)
    reg_field_registry_tools(server)
    reg_integration_diagnostics_tools(server)
    reg_monitoring_tools(server)
    reg_pipeline_runs_tools(server)
    reg_posture_tools(server)
    reg_reliability_tools(server)
    reg_reports_tools(server)
    reg_scheduler_tools(server)
    reg_settings_tools(server)
    reg_sources_tools(server)
    reg_suggestion_tools(server)
    reg_unified_fields_tools(server)
    reg_schema(server)
    reg_config(server)
    reg_stats(server)
    reg_docs(server)
    reg_prompts(server)


class _AssetAtlasFastMCP(FastMCP):
    """FastMCP plus the process-level app context and one ASGI middleware on
    the Streamable HTTP app."""

    def streamable_http_app(self):
        # `add_middleware` prepends, so ClientAddressMiddleware ends up OUTSIDE
        # the SDK's AuthenticationMiddleware and has recorded the caller's
        # address by the time token verification runs. MCPJWTTokenVerifier's
        # API-key branch needs it to stamp `api_keys.last_used_ip`; the SDK
        # hands `verify_token` the token and nothing else. FastMCP builds the
        # Starlette app itself and exposes no middleware hook, so overriding
        # the factory it calls at startup is the seam. stdio never calls this.
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        from src.mcp.auth import ClientAddressMiddleware

        app = super().streamable_http_app()
        # Honour nginx's X-Forwarded-Proto so the `/mcp/` -> `/mcp` 307 that
        # Starlette's redirect_slashes builds keeps `https` (#1448). uvicorn
        # only applies it for peers in `forwarded_allow_ips` (127.0.0.1), and
        # nginx is another container. Trusting every peer is safe for the same
        # reason the Host check is off: the port is reachable only through
        # nginx, which overwrites the header with its own $scheme. Added
        # BEFORE ClientAddressMiddleware so that one stays outermost and reads
        # the untouched socket peer; X-Real-IP is what it prefers anyway.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
        app.add_middleware(ClientAddressMiddleware)

        # The DB pool, Redis and services live for the PROCESS, not per MCP
        # session: the SDK enters `app_lifespan` inside every `Server.run()`,
        # i.e. per session, and API-key auth runs before any session exists
        # (#1448). The Starlette lifespan runs once per process, so the
        # process scope is wrapped around the SDK's own (session_manager.run).
        sdk_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(starlette_app):
            async with app_context_process_scope():
                async with sdk_lifespan(starlette_app) as state:
                    yield state

        app.router.lifespan_context = lifespan
        return app


def create_server(transport: str = "stdio", port: int = 8100) -> FastMCP:
    """Create and configure the MCP server instance.

    Args:
        transport: "stdio" (no auth) or "streamable-http" (JWT / API-key auth).
        port: HTTP port when using streamable-http transport.
    """
    kwargs = dict(
        name="AssetAtlas",
        lifespan=app_lifespan,
        json_response=True,
    )

    if transport == "streamable-http":
        from pydantic import AnyHttpUrl
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.transport_security import TransportSecuritySettings
        from src.mcp.auth import MCPJWTTokenVerifier

        api_url = os.getenv("API_URL", f"http://localhost:{port}")
        kwargs["token_verifier"] = MCPJWTTokenVerifier()
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(api_url),
            resource_server_url=AnyHttpUrl(api_url),
            # Any valid JWT or aa_ API key is accepted; role checked by tools
            # if needed. No tool gates on a scope, so a key connected here is
            # in practice an admin-level read credential (#1158).
            required_scopes=[],
        )
        kwargs["host"] = "0.0.0.0"
        kwargs["port"] = port
        # Stateless: every POST is served on its own and no session outlives
        # its request. SDK 1.x never frees a session its client DELETEs and
        # has no idle timeout, so a client that opens a session per tool call
        # leaked one each time (#1448). Every tool is a read-only one-shot, so
        # nothing is lost; the per-request lifespan only borrows the process
        # context. A DELETE is answered 405, which the spec allows.
        kwargs["stateless_http"] = True
        # DNS-rebinding protection OFF, and said so explicitly: FastMCP turns
        # it on with a localhost-only Host allowlist whenever it is built for
        # a loopback host with no settings, and nginx forwards the public
        # `Host`, so every proxied request got a 421 (#1448). The check guards
        # a browser page reaching a local server with ambient credentials;
        # here the port is container-internal (`expose`d, never published),
        # reachable only through nginx, and every request must carry a bearer
        # token in a header — a rebinding page has none and gets a 401 anyway.
        # Explicit settings also stop a future SDK default from re-enabling it.
        kwargs["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )

    server = _AssetAtlasFastMCP(**kwargs)

    _register_all(server)
    return server


# Module-level instance for stdio transport (used by `python -m src.mcp.server`
# and Claude Code MCP config). No auth required for local stdio.
mcp = create_server(transport="stdio")


def configure_logging(transport: str) -> None:
    """Configure logging. All output goes to stderr (stdout reserved for MCP protocol)."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Quiet noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse CLI args for transport and port."""
    parser = argparse.ArgumentParser(description="AssetAtlas MCP Server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Use Streamable HTTP transport (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8100")),
        help="HTTP port (default: 8100)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    env_transport = os.getenv("MCP_TRANSPORT", "").lower()
    if args.http or env_transport == "http":
        transport = "streamable-http"
    else:
        transport = "stdio"

    configure_logging(transport)
    logger.info(f"Starting AssetAtlas MCP server (transport={transport})")

    if transport == "streamable-http":
        # Create a new server instance with auth enabled
        server = create_server(transport=transport, port=args.port)
        logger.info(f"JWT / API-key auth enabled, listening on port {args.port}")
    else:
        server = mcp

    server.run(transport=transport)


if __name__ == "__main__":
    main()
