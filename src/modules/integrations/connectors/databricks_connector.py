# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Databricks Connector

Two source modes, selected by the ``mode`` config key:

- ``inventory`` (default): collects compute and data platform assets from
  Databricks workspaces via the REST API: clusters, jobs, SQL warehouses, and
  (optionally) Unity Catalog objects (catalogs, schemas, tables).
- ``sql``: executes a user-supplied SQL statement on a SQL warehouse via the
  Statement Execution API (submit, poll to a terminal state, page through
  result chunks) and yields the result rows as records — the SIEM/datalake
  transport path that vendor tiles without a connector of their own run
  their query through.

Supports PAT (Personal Access Token) and OAuth M2M authentication.
"""

from typing import Dict, Iterator, NoReturn, Optional, Any

import requests
import time

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import (
    APIKeyAuth,
    OAuth2ClientCredentials,
    CursorPaginator,
    AdaptiveRateLimiter,
    ConfigSchema,
)


@ConnectorRegistry.register
class DatabricksConnector(BaseConnector):
    """
    Databricks Connector

    In inventory mode, fetches clusters, jobs, SQL warehouses, and Unity
    Catalog assets from a Databricks workspace. In SQL mode, runs a
    user-supplied statement on a SQL warehouse and yields the result rows.
    Requires a Personal Access Token or OAuth M2M credentials with
    appropriate permissions.
    """

    integration_type = "databricks"
    display_name = "Databricks"
    category = "Data Platform"
    description = "Collect compute and data assets from Databricks workspaces"
    logo_url = "/logos/Databricks.svg"
    # Transport role is what the wizard's SIEM/datalake transport picker
    # filters on; the SQL source mode (config ``mode = "sql"``) is the query
    # path that backs it (#1318).
    connector_role = "transport"

    # Statement Execution API status.state values.
    STATEMENT_STATE_SUCCEEDED = "SUCCEEDED"
    STATEMENT_STATE_FAILED = "FAILED"
    # Terminal states that produce no result and carry no error detail
    # (FAILED is terminal too, but carries status.error).
    STATEMENT_STATES_DEAD = ("CANCELED", "CLOSED")

    # Statements executed on an HTTP request path — test_connection() and
    # the wizard's sample-data preview — cannot wait out a cold warehouse
    # start: the browser abandons the request after 30s
    # (ui/prod/src/api/client.ts). 15s keeps the whole exchange (warehouse
    # lookup, the submit call's own 10s server-side wait, polling, and the
    # cancel round-trip) inside that budget. The scheduled path keeps the
    # full poll_timeout — it has no HTTP caller to answer to and must
    # tolerate a multi-minute cold start.
    REQUEST_PATH_POLL_TIMEOUT = 15

    def __init__(self, config: Dict[str, Any]):
        self.workspace_url = config.get("workspace_url", "").rstrip("/")
        self.page_size = int(config.get("page_size", 100))
        self.collect_unity_catalog = config.get("collect_unity_catalog", False)
        self.mode = config.get("mode", "inventory")
        if self.mode not in ("inventory", "sql"):
            raise ValueError(
                f"Unsupported mode '{self.mode}' — expected 'inventory' or 'sql'"
            )
        # SQL mode settings. Unused in inventory mode.
        self.warehouse_id = config.get("warehouse_id", "")
        self.sql_statement = (config.get("sql_statement") or "").strip()
        self.default_catalog = config.get("catalog") or None
        self.default_schema = config.get("schema") or None
        self.poll_interval = int(config.get("poll_interval", 5))
        # 900s default: a stopped warehouse is the normal case (they auto-stop
        # when idle) and Databricks starts it automatically on submission,
        # which alone can take several minutes — during which the statement
        # sits in PENDING — before the query itself even runs. A Splunk-style
        # 300s would fail cold starts that were going to succeed.
        self.poll_timeout = int(config.get("poll_timeout", 900))
        if self.mode == "sql":
            if not self.warehouse_id:
                raise ValueError(
                    "warehouse_id is required when mode is 'sql' — it is the "
                    "identifier in the warehouse's HTTP path "
                    "(/sql/1.0/warehouses/<id>)"
                )
            if not self.sql_statement:
                raise ValueError(
                    "sql_statement is required when mode is 'sql' — the query "
                    "must return pre-normalized asset rows (hostname, ip, mac, "
                    "sn, os, ...; see docs/features/integrations/via-siem.md)"
                )
        # Per-endpoint page caps handed to CursorPaginator. Named here rather
        # than inlined at the call sites so the truncation path can be driven
        # in a test without mocking hundreds of HTTP pages.
        self._max_pages = {
            "clusters": 100,
            "jobs": 500,
            "warehouses": 100,
            "catalogs": 100,
            "schemas": 100,
            "tables": 500,
        }
        super().__init__(config)

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_url(
                "workspace_url",
                "Workspace URL",
                required=True,
                placeholder="https://adb-1234567890123456.7.azuredatabricks.net",
                description="Databricks workspace URL",
            )
            .add_select(
                "mode",
                "Source Mode",
                options=[
                    {"value": "inventory", "label": "Inventory (workspace assets)"},
                    {"value": "sql", "label": "SQL query (via SIEM/datalake)"},
                ],
                default="inventory",
                required=True,
                description=(
                    "Inventory collects clusters, jobs, SQL warehouses and Unity Catalog "
                    "objects. SQL executes the statement below on a SQL warehouse and "
                    "ingests the result rows (SIEM/datalake transport)."
                ),
            )
            .add_select(
                "auth_type",
                "Authentication Method",
                options=[
                    {"value": "pat", "label": "Personal Access Token (PAT)"},
                    {"value": "oauth", "label": "OAuth (Machine-to-Machine)"},
                ],
                default="pat",
                required=True,
                description="Authentication method for the Databricks API",
            )
            .add_password(
                "token",
                "Personal Access Token",
                required=False,
                description="Databricks PAT (required if auth method is PAT)",
            )
            .add_string(
                "client_id",
                "OAuth Client ID",
                required=False,
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                description="Service principal application ID (required if auth method is OAuth)",
            )
            .add_password(
                "client_secret",
                "OAuth Client Secret",
                required=False,
                description="Service principal secret (required if auth method is OAuth)",
            )
            .add_string(
                "warehouse_id",
                "SQL Warehouse ID",
                required=False,
                placeholder="a1b2c3d4e5f60718",
                description=(
                    "Warehouse to run the statement on (required in SQL mode). The "
                    "identifier in the warehouse's HTTP path: /sql/1.0/warehouses/<id>"
                ),
            )
            .add_string(
                "sql_statement",
                "SQL Statement",
                required=False,
                placeholder="SELECT hostname, ip, mac, sn, os, manufacturer, model, vendor_category, last_time FROM ...",
                description=(
                    "Query returning pre-normalized asset rows — column names must match "
                    "the via-SIEM schema (hostname, fqdn, ip, mac, sn, os, manufacturer, "
                    "model, vendor_category, first_time, last_time). Required in SQL mode."
                ),
            )
            .add_string(
                "catalog",
                "Default Catalog",
                required=False,
                description="Default catalog for the statement, like USE CATALOG (optional, SQL mode only)",
            )
            .add_string(
                "schema",
                "Default Schema",
                required=False,
                description="Default schema for the statement, like USE SCHEMA (optional, SQL mode only)",
            )
            .add_number(
                "poll_interval",
                "Poll Interval (seconds)",
                default=5,
                min_val=1,
                max_val=60,
                description="Seconds between statement status checks (SQL mode)",
            )
            .add_number(
                "poll_timeout",
                "Poll Timeout (seconds)",
                default=900,
                min_val=60,
                max_val=7200,
                description=(
                    "Maximum seconds to wait for the statement to finish, including "
                    "warehouse start-up (SQL mode). On timeout the statement is "
                    "cancelled server-side."
                ),
            )
            .add_boolean(
                "collect_unity_catalog",
                "Collect Unity Catalog Assets",
                default=False,
                description="Collect catalogs, schemas, and tables from Unity Catalog (requires UC-enabled workspace)",
            )
            .add_number(
                "page_size",
                "Page Size",
                default=100,
                min_val=10,
                max_val=1000,
                description="Number of records per API request",
            )
            .build()
        )

    def _build_auth(self):
        auth_type = self.config.get("auth_type", "pat")

        if auth_type == "pat":
            token = self.config.get("token")
            if not token:
                raise ValueError("Personal Access Token (token) is required for PAT authentication")
            return APIKeyAuth(
                {
                    "api_key": token,
                    "api_key_header": "Authorization",
                    "api_key_prefix": "Bearer ",
                    "base_url": self.workspace_url,
                },
                self.logger,
            )

        if auth_type == "oauth":
            client_id = self.config.get("client_id")
            client_secret = self.config.get("client_secret")
            if not client_id:
                raise ValueError("client_id is required for OAuth authentication")
            if not client_secret:
                raise ValueError("client_secret is required for OAuth authentication")
            return OAuth2ClientCredentials(
                {
                    "token_url": f"{self.workspace_url}/oidc/v1/token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "all-apis",
                    "base_url": self.workspace_url,
                },
                self.logger,
            )

        raise ValueError(f"Unsupported auth_type: {auth_type}")

    def _build_rate_limiter(self):
        return AdaptiveRateLimiter(initial_rpm=30)

    # ------------------------------------------------------------------
    # Failure diagnosis
    # ------------------------------------------------------------------

    @staticmethod
    def _error_status(exc: Exception) -> Optional[int]:
        """HTTP status behind a transport exception, or None if it never got
        a response (DNS failure, timeout, retries exhausted)."""
        return getattr(getattr(exc, "response", None), "status_code", None)

    @staticmethod
    def _is_token_endpoint(exc: Exception) -> bool:
        """True when the failing call was the OIDC token exchange itself."""
        url = (
            getattr(getattr(exc, "response", None), "url", None)
            or getattr(getattr(exc, "request", None), "url", None)
            or ""
        )
        return "/oidc/v1/token" in str(url)

    @staticmethod
    def _exc_url(exc: Exception) -> str:
        """The URL a transport exception was calling, or '' if the request
        never got that far (DNS failure, connect timeout)."""
        return str(
            getattr(getattr(exc, "response", None), "url", None)
            or getattr(getattr(exc, "request", None), "url", None)
            or ""
        )

    @staticmethod
    def _api_error_text(exc: Exception) -> str:
        """The API's own words for a failure, or '' when it gave none.

        Databricks REST answers ``{"error_code": ..., "message": ...}``; the
        OIDC token endpoint answers the OAuth2 ``{"error": ...,
        "error_description": ...}``. Never invent text — an empty string is
        better than a guess, and the API's wording is what makes a diagnostic
        actionable.
        """
        response = getattr(exc, "response", None)
        if response is None:
            return ""
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            code = body.get("error_code") or body.get("error")
            detail = body.get("message") or body.get("error_description")
            named = ": ".join(str(part) for part in (code, detail) if part)
            if named:
                return named
            # A JSON body in a shape we don't recognise (a gateway's own
            # {"detail": ...}, say) still carries the server's words — fall
            # through to the raw text rather than reporting nothing.
        return (getattr(response, "text", "") or "").strip()[:200]

    def _is_credential_rejection(self, exc: Exception) -> bool:
        """True when Databricks could not authenticate us at all (HTTP 401).

        A 403 is object-scoped — "this principal may not read that catalog" —
        and stays a degradation, so one locked-down catalog cannot abort a
        whole collection. A 401 says we hold no working credential, and
        carrying on would publish a partial inventory as a complete one.

        Known gap, deliberate: `_diagnose` maps 401 *and* 403 to the
        credential message because some workspaces answer 403 for a dead PAT,
        so on one of those a token that dies **mid-walk** degrades per level
        here and the run still completes green with partial Unity Catalog
        data. Widening this to 403 would abort on a merely locked-down
        catalog, which is the case the per-level degradation exists for.
        The core clusters/jobs/warehouses collection runs first and fails
        loudly, so this only bites a token that expires during the UC walk.
        """
        return self._error_status(exc) == 401

    def _diagnose(self, exc: Exception, operation: str) -> str:
        """Turn a transport failure into a sentence that names the cause.

        Shared by test_connection() and the collection path so a dead token
        reads the same in the wizard and in ``integrations.last_error``.
        """
        status = self._error_status(exc)
        detail = self._api_error_text(exc)
        # Databricks' own messages usually end in a period; don't double it.
        said = f" Databricks said: {detail.rstrip('.')}." if detail else ""

        # A rejection at the token endpoint is about the client secret, not an
        # expired access token — OAuth M2M mints a fresh one on every run.
        if status in (400, 401, 403) and self._is_token_endpoint(exc):
            return (
                f"Databricks rejected the OAuth client credentials at "
                f"{self.workspace_url}/oidc/v1/token (HTTP {status}). The "
                f"service principal's client secret is wrong or has been "
                f"rotated, or the principal no longer has access to this "
                f"workspace.{said}"
            )

        # Databricks answers 401 for a credential it cannot authenticate and
        # 403 for one it authenticates but will not honour; a PAT that has
        # expired or been revoked surfaces as either, depending on workspace
        # and endpoint, so both lead with the credential explanation.
        if status in (401, 403):
            if self.config.get("auth_type") == "oauth":
                return (
                    f"Databricks rejected the OAuth access token for "
                    f"{operation} (HTTP {status}). The service principal's "
                    f"workspace access may have been revoked, or it lacks "
                    f"permission for this API.{said}"
                )
            return (
                f"Databricks rejected the Personal Access Token for "
                f"{operation} (HTTP {status}). The token may have expired or "
                f"been revoked, or it may lack permission for this API. Issue "
                f"a new token under Settings → Developer → Access tokens and "
                f"update the integration.{said}"
            )

        # SQL-mode 404s: the missing object is the SQL warehouse (or, on a
        # statement poll, an expired statement) — not the workspace URL the
        # generic 404 branch below points at. Checked before it.
        url = self._exc_url(exc)
        if status == 404 and "/api/2.0/sql/warehouses/" in url:
            return (
                f"SQL warehouse '{self.warehouse_id}' was not found (HTTP "
                f"404). Check the warehouse ID — it is the identifier in the "
                f"warehouse's HTTP path (/sql/1.0/warehouses/<id>) — and that "
                f"the credentials have CAN USE on it.{said}"
            )
        if status == 404 and "/api/2.0/sql/statements" in url:
            return (
                f"Databricks returned 404 for {operation}. Either SQL "
                f"warehouse '{self.warehouse_id}' does not exist, or the "
                f"statement expired server-side (statements are removed about "
                f"12 hours after reaching a terminal state).{said}"
            )

        if status == 404:
            return (
                f"Databricks returned 404 for {operation}. Check the workspace "
                f"URL '{self.workspace_url}' and that this API is enabled on "
                f"the workspace — the credentials were accepted.{said}"
            )

        if status is not None and status >= 500:
            return (
                f"Databricks returned a server error (HTTP {status}) for "
                f"{operation}. The workspace is unhealthy or under "
                f"maintenance; the credentials are not the problem.{said}"
            )

        if isinstance(exc, requests.exceptions.RetryError):
            # Retries are exhausted before a response survives, so there is no
            # status left to read — the status lives in the urllib3 text.
            return (
                f"Databricks never returned a usable response for {operation}; "
                f"every retry came back as a server error or a rate limit. The "
                f"credentials are not the problem. Underlying error: {exc}"
            )

        if isinstance(exc, (requests.exceptions.ConnectionError,
                            requests.exceptions.Timeout,
                            ConnectionError, TimeoutError)):
            return (
                f"Could not reach the Databricks workspace at "
                f"{self.workspace_url} for {operation}. Check the URL, DNS and "
                f"any firewall or proxy in between — the request never got far "
                f"enough to use the credentials. Underlying error: {exc}"
            )

        # Everything else — 400 METASTORE_DOES_NOT_EXIST, 409, a non-retried
        # 429. str(exc) is only the status line, so the API's own words are
        # the whole diagnosis here; dropping `said` would leave the operator
        # with nothing at all.
        return f"{operation} failed: {exc}{said}"

    def _raise_diagnosed(self, exc: Exception, operation: str) -> NoReturn:
        """Re-raise ``exc`` carrying a message that names the cause.

        The failure is never swallowed and the type never changes — only the
        text is rewritten, because ``str(exc)`` is what integration_service
        stores as the integration's ``last_error``, and "403 Client Error:
        Forbidden for url: ..." tells an operator nothing about an expired
        token. The response rides along so a caller can still classify what
        came back.
        """
        message = self._diagnose(exc, operation)
        diagnosed = None
        try:
            if isinstance(exc, requests.RequestException):
                diagnosed = type(exc)(
                    message, response=exc.response, request=exc.request,
                )
            else:
                diagnosed = type(exc)(message)
        except Exception:
            # An exception whose constructor won't take a bare message (e.g.
            # JSONDecodeError). Better the original than a TypeError raised
            # from the error path itself.
            pass

        if diagnosed is None:
            raise exc
        raise diagnosed from exc

    def test_connection(self) -> Dict[str, Any]:
        if self.mode == "sql":
            return self._test_connection_sql()
        try:
            self.logger.info(f"Testing connection to Databricks workspace: {self.workspace_url}")

            self.auth.authenticate()
            response = self.auth.request("/api/2.0/clusters/list")
            data = response.json()
            clusters = data.get("clusters", [])

            return {
                "success": True,
                "message": (
                    f"Successfully connected to Databricks workspace. "
                    f"Found {len(clusters)} cluster(s)."
                ),
                "metadata": {
                    "workspace_url": self.workspace_url,
                    "cluster_count": len(clusters),
                },
            }

        except Exception as e:
            # This message is persisted verbatim as the integration's
            # last_error, so it has to name the cause, not echo a status line.
            message = self._diagnose(e, "GET /api/2.0/clusters/list")
            self.logger.error(f"Connection test failed: {message}")
            return {
                "success": False,
                "message": message,
                "error": str(e),
            }

    def _test_connection_sql(self) -> Dict[str, Any]:
        try:
            self.logger.info(
                f"Testing Databricks SQL mode on warehouse {self.warehouse_id}"
            )

            self.auth.authenticate()

            # Verify the warehouse exists and report its state. This doubles
            # as the permission check: a principal without CAN USE on the
            # warehouse gets a 404 here, before any statement runs. STOPPED
            # is not a failure — Databricks starts the warehouse
            # automatically when a statement is submitted.
            try:
                warehouse = self.auth.request(
                    f"/api/2.0/sql/warehouses/{self.warehouse_id}"
                ).json()
            except Exception as e:
                self._raise_diagnosed(
                    e, f"GET /api/2.0/sql/warehouses/{self.warehouse_id}"
                )
            warehouse_state = warehouse.get("state", "UNKNOWN")

            # Prove the whole path end to end: submit, poll, terminal state.
            # Bounded by REQUEST_PATH_POLL_TIMEOUT, not poll_timeout: this
            # runs on an HTTP request the browser abandons after 30s, so
            # waiting out a cold warehouse start would end in a generic
            # client-side timeout — while the statement kept polling and
            # burning warehouse time for a caller that is no longer
            # listening.
            self._execute_statement(
                "SELECT 1", poll_timeout=self.REQUEST_PATH_POLL_TIMEOUT
            )

            cold_start_note = (
                " It was STOPPED and started automatically for the test — "
                "scheduled runs will see the same cold-start delay."
                if warehouse_state == "STOPPED" else ""
            )
            return {
                "success": True,
                "message": (
                    f"Successfully connected to Databricks workspace. SQL "
                    f"warehouse '{warehouse.get('name')}' is {warehouse_state} "
                    f"and a test statement executed successfully."
                    f"{cold_start_note}"
                ),
                "metadata": {
                    "workspace_url": self.workspace_url,
                    "warehouse_id": self.warehouse_id,
                    "warehouse_state": warehouse_state,
                },
            }

        except (RuntimeError, TimeoutError) as e:
            # Raised by the statement path itself with a fully-composed
            # message (statement FAILED, cancelled, or poll timeout).
            #
            # A request-path timeout lands here as success=False even
            # though nothing is known to be wrong with the configuration —
            # deliberately. success=True would claim a path we cancelled
            # before proving, and the wizard treats success as the cue to
            # fetch sample data (ConnectionTestStep.tsx), which would hit
            # the same bound and then report "no sample data" for a
            # warehouse that was merely starting. The message says the
            # configuration is not indicted and that a scheduled run
            # carries no such limit, and the wizard's "skip connection
            # test" checkbox leaves the operator unblocked.
            message = str(e)
            self.logger.error(f"Connection test failed: {message}")
            return {
                "success": False,
                "message": message,
                "error": str(e),
            }
        except Exception as e:
            # This message is persisted verbatim as the integration's
            # last_error, so it has to name the cause, not echo a status line.
            message = self._diagnose(e, "SQL connection test")
            self.logger.error(f"Connection test failed: {message}")
            return {
                "success": False,
                "message": message,
                "error": str(e),
            }

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        try:
            self.auth.authenticate()
        except Exception as e:
            self._raise_diagnosed(e, "authentication")
        if self.mode == "sql":
            # batch_id "sample" is BaseConnector.get_sample_data() — the
            # wizard's field-mapping preview — which runs on the same 30s
            # HTTP budget as test_connection(), so the statement is bounded
            # by REQUEST_PATH_POLL_TIMEOUT. A real batch id means the
            # scheduler: no HTTP caller, and the full configured
            # poll_timeout for cold warehouse starts.
            yield from self._collect_sql_records(
                poll_timeout=(
                    self.REQUEST_PATH_POLL_TIMEOUT
                    if batch_id == "sample" else None
                )
            )
            return
        yield from self._collect_resource(
            "clusters", self._max_pages["clusters"], "/api/2.0/clusters/list",
            "clusters", self._normalize_cluster,
        )
        yield from self._collect_resource(
            "jobs", self._max_pages["jobs"], "/api/2.1/jobs/list",
            "jobs", self._normalize_job,
            extra_params={"limit": self.page_size},
            use_has_more=True,
        )
        yield from self._collect_resource(
            "SQL warehouses", self._max_pages["warehouses"], "/api/2.0/sql/warehouses",
            "warehouses", self._normalize_warehouse,
        )
        if self.collect_unity_catalog:
            yield from self._collect_unity_catalog()

    # ------------------------------------------------------------------
    # SQL mode (Statement Execution API)
    # ------------------------------------------------------------------

    def _collect_sql_records(self, poll_timeout: Optional[int] = None
                             ) -> Iterator[Dict[str, Any]]:
        """Run the configured statement and yield one dict per result row.

        Rows arrive pre-normalized (the via-SIEM v1 contract in
        docs/features/integrations/via-siem.md): the query's column names
        already match the core asset schema, so the manifest's column names
        are zipped onto each row array verbatim — no renaming, no inference.

        ``poll_timeout`` is the request-path bound when the caller answers
        to a browser (sample-data preview); None is the scheduled path,
        which polls under the configured ``self.poll_timeout``.
        """
        self.logger.info(
            f"Executing SQL statement on warehouse {self.warehouse_id}"
        )
        final = self._execute_statement(
            self.sql_statement, poll_timeout=poll_timeout
        )

        manifest = final.get("manifest") or {}
        columns = [
            column.get("name")
            for column in (manifest.get("schema") or {}).get("columns", [])
        ]
        if not columns:
            # A statement with no result columns (DDL, an empty projection)
            # succeeded but has nothing to ingest.
            self.logger.warning(
                "Databricks statement succeeded but returned no column "
                "manifest — no rows to ingest"
            )
            return
        if manifest.get("truncated"):
            self.logger.warning(
                "Databricks truncated the statement result (byte/row limit) — "
                "the ingested rows are a partial result set"
            )

        total_rows = 0
        # The first chunk rides on the statement response; the rest are
        # fetched by following next_chunk_internal_link until it runs out —
        # stopping at chunk 0 would silently drop everything past it.
        chunk = final.get("result") or {}
        while True:
            for row in chunk.get("data_array") or []:
                total_rows += 1
                yield {
                    columns[i]: value
                    for i, value in enumerate(row)
                    if i < len(columns)
                }
            next_link = chunk.get("next_chunk_internal_link")
            if not next_link:
                break
            chunk = self._fetch_result_chunk(next_link)
        self.logger.info(
            f"Databricks statement returned {total_rows} row(s)"
        )

    def _execute_statement(self, sql: str, poll_timeout: Optional[int] = None
                           ) -> Dict[str, Any]:
        """Submit ``sql`` and poll to a terminal state; return the final
        statement response (manifest + first result chunk on SUCCEEDED).

        ``poll_timeout`` overrides the configured timeout for HTTP request
        paths (see REQUEST_PATH_POLL_TIMEOUT); None means the configured
        ``self.poll_timeout``, which the scheduled path relies on to
        survive a cold warehouse start.

        Raises RuntimeError on a FAILED/CANCELED/CLOSED statement and
        TimeoutError on poll timeout (after cancelling server-side).
        """
        data = self._submit_statement(sql)
        statement_id = data.get("statement_id")
        if not statement_id:
            raise RuntimeError(
                "Databricks accepted the statement but returned no "
                "statement_id — there is nothing to poll for the result."
            )
        state = self._statement_state(data)
        if state == self.STATEMENT_STATE_SUCCEEDED:
            return data
        self._raise_on_dead_statement(statement_id, state, data)
        return self._poll_statement(statement_id, poll_timeout)

    def _submit_statement(self, sql: str) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "statement": sql,
            "warehouse_id": self.warehouse_id,
            "format": "JSON_ARRAY",
            # INLINE fails a statement whose result exceeds 25 MiB; the
            # via-SIEM queries this mode runs return aggregated per-asset
            # rows, so that ceiling is the right default — EXTERNAL_LINKS
            # would need separate unauthenticated chunk downloads.
            "disposition": "INLINE",
            # Hybrid mode: wait up to 10s server-side so fast queries return
            # their result inline, then fall back to async polling.
            "wait_timeout": "10s",
            "on_wait_timeout": "CONTINUE",
        }
        if self.default_catalog:
            body["catalog"] = self.default_catalog
        if self.default_schema:
            body["schema"] = self.default_schema
        try:
            return self.auth.request(
                "/api/2.0/sql/statements", method="POST", json=body,
            ).json()
        except Exception as e:
            self._raise_diagnosed(e, "POST /api/2.0/sql/statements")

    def _poll_statement(self, statement_id: str,
                        poll_timeout: Optional[int] = None) -> Dict[str, Any]:
        # None is the scheduled path: the full configured timeout, which a
        # cold warehouse start needs. An explicit value is an HTTP request
        # path bounding itself inside the browser's 30s — the message on
        # timeout differs accordingly.
        timeout = self.poll_timeout if poll_timeout is None else poll_timeout
        start_time = time.time()
        last_state = None

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                # An abandoned statement keeps burning warehouse time and
                # money, so cancel before raising.
                self._cancel_statement(statement_id)
                if poll_timeout is not None:
                    raise TimeoutError(
                        self._request_path_timeout_message(
                            statement_id, timeout, last_state
                        )
                    )
                raise TimeoutError(
                    self._statement_timeout_message(statement_id, last_state)
                )

            try:
                data = self.auth.request(
                    f"/api/2.0/sql/statements/{statement_id}"
                ).json()
            except Exception as e:
                self._raise_diagnosed(
                    e, f"GET /api/2.0/sql/statements/{statement_id}"
                )

            state = self._statement_state(data)
            if state == self.STATEMENT_STATE_SUCCEEDED:
                return data
            self._raise_on_dead_statement(statement_id, state, data)

            if state != last_state:
                if state == "PENDING":
                    # The statement is queued behind a warehouse that is
                    # (re)starting — the normal cold-start case, minutes,
                    # not an error. Say so rather than looking hung.
                    self.logger.info(
                        f"Statement {statement_id} is queued — the SQL "
                        f"warehouse is starting (a stopped warehouse starts "
                        f"automatically on submission; this can take several "
                        f"minutes)"
                    )
                else:
                    self.logger.info(
                        f"Statement {statement_id} state: {state}"
                    )
                last_state = state

            # Never sleep past the deadline: poll_interval (up to 60s) can
            # exceed what is left of the bound — the request path's 15s
            # against a configured 60s interval — and oversleeping the
            # cancel is the same as having no bound at all.
            remaining = timeout - (time.time() - start_time)
            time.sleep(min(self.poll_interval, max(remaining, 0.0)))

    def _statement_timeout_message(self, statement_id: str,
                                   last_state: Optional[str]) -> str:
        """Distinguish "the warehouse never came up" from "the query was
        still running" — the operator action is completely different."""
        base = (
            f"Databricks statement {statement_id} did not finish within the "
            f"{self.poll_timeout}s poll timeout and was cancelled server-side."
        )
        if last_state == "RUNNING":
            return (
                f"{base} The warehouse was up and the query was still "
                f"executing — optimise the statement or raise poll_timeout."
            )
        # Only ever PENDING (or nothing observed): the query never began.
        return (
            f"{base} The SQL warehouse was still starting — the statement "
            f"never began executing. A stopped warehouse can take several "
            f"minutes to come up; raise poll_timeout or start the warehouse "
            f"before the run."
        )

    def _request_path_timeout_message(self, statement_id: str, timeout: int,
                                      last_state: Optional[str]) -> str:
        """The message for a statement cancelled at the request-path bound.

        Deliberately different from the scheduled-path timeout: the bound
        that fired is ours (the browser's 30s budget), not the configured
        poll_timeout, so "raise poll_timeout" would be bad advice. The
        configuration has not been indicted either — the warehouse lookup
        and the submit both succeeded — so the message says what is
        actually known: the warehouse was coming up (or the query was
        still running), nothing is proven wrong, and a scheduled run
        carries no such limit.
        """
        base = (
            f"Databricks statement {statement_id} did not finish within "
            f"the {timeout}s limit applied to connection tests and data "
            f"previews and was cancelled server-side."
        )
        if last_state == "RUNNING":
            return (
                f"{base} The warehouse was up and the query was still "
                f"executing. Nothing is known to be wrong with the "
                f"configuration, and a scheduled run carries no such "
                f"limit — it waits up to the configured "
                f"{self.poll_timeout}s poll timeout. Retry when the "
                f"warehouse is less busy."
            )
        # Only ever PENDING (or nothing observed): the query never began —
        # the normal cold-start case.
        return (
            f"{base} The SQL warehouse was still starting — the statement "
            f"never began executing (a stopped warehouse starts "
            f"automatically on submission, which can take several "
            f"minutes). Nothing is known to be wrong with the "
            f"configuration — the warehouse was found and the statement "
            f"was accepted — and a scheduled run carries no such limit: "
            f"it waits up to the configured {self.poll_timeout}s poll "
            f"timeout. Start the warehouse and retry the test, or save "
            f"the integration and let the first scheduled run absorb the "
            f"cold start."
        )

    def _cancel_statement(self, statement_id: str) -> None:
        """Best-effort server-side cancel. A failure here is logged, not
        raised — the timeout error about to be raised is the one that
        matters."""
        try:
            self.auth.request(
                f"/api/2.0/sql/statements/{statement_id}/cancel", method="POST",
            )
            self.logger.info(
                f"Cancelled Databricks statement {statement_id} after the "
                f"poll timeout"
            )
        except Exception as e:
            self.logger.warning(
                f"Could not cancel Databricks statement {statement_id} "
                f"server-side — it may keep running on the warehouse: {e}"
            )

    def _raise_on_dead_statement(self, statement_id: str, state: str,
                                 data: Dict[str, Any]) -> None:
        """Raise for a statement that reached a non-success terminal state.

        A FAILED statement arrives as HTTP 200 — the failure detail lives in
        ``status.error``, and the API's own error code and message are what
        makes a bad query fixable, so they are surfaced verbatim.
        """
        if state == self.STATEMENT_STATE_FAILED:
            error = (data.get("status") or {}).get("error") or {}
            detail = ": ".join(
                str(part)
                for part in (error.get("error_code"), error.get("message"))
                if part
            ) or "no error detail returned"
            raise RuntimeError(
                f"Databricks statement {statement_id} FAILED: {detail}"
            )
        if state in self.STATEMENT_STATES_DEAD:
            raise RuntimeError(
                f"Databricks statement {statement_id} reached terminal state "
                f"{state} before producing a result — was it cancelled "
                f"elsewhere (the Databricks SQL editor, a warehouse restart)?"
            )

    @staticmethod
    def _statement_state(data: Dict[str, Any]) -> str:
        return (data.get("status") or {}).get("state", "UNKNOWN")

    def _fetch_result_chunk(self, link: str) -> Dict[str, Any]:
        """Fetch one result chunk by its internal link
        (``/api/2.0/sql/statements/{id}/result/chunks/{n}``)."""
        try:
            return self.auth.request(link).json()
        except Exception as e:
            self._raise_diagnosed(e, f"GET {link}")

    # ------------------------------------------------------------------
    # Shared fetch/collect helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, url, result_key, cursor,
                    extra_params=None, use_has_more=False):
        params = dict(extra_params or {})
        if cursor:
            params["page_token"] = cursor
        try:
            data = self.auth.request(url, params=params).json()
        except Exception as e:
            # Every collection call funnels through here, so this is the one
            # place a mid-run credential failure has to be named. The run
            # still fails — only the text changes.
            self._raise_diagnosed(e, f"GET {url}")
        records = data.get(result_key, [])
        if use_has_more:
            next_token = data.get("next_page_token") if data.get("has_more") else None
        else:
            next_token = data.get("next_page_token")
        return records, next_token

    def _paginate(self, label, max_pages, url, result_key,
                  extra_params=None, use_has_more=False) -> Iterator[dict]:
        """Yield raw records from a cursor-paginated endpoint, warning when
        the page cap — rather than the end of the data — is what stopped us.

        ``CursorPaginator`` (base/pagination.py) simply stops at ``max_pages``
        and emits no signal, so "collected everything" and "collected the
        first N pages" are indistinguishable to the caller. Track what the
        last page handed back and say so; otherwise a large estate silently
        reports a partial inventory as a complete one.
        """
        paginator = CursorPaginator(max_pages=max_pages)
        state = {"pages": 0, "more": False}

        def fetch(cursor):
            records, next_token = self._fetch_page(
                url, result_key, cursor, extra_params, use_has_more,
            )
            state["pages"] += 1
            # Mirrors the paginator's own continue condition: a page with
            # records AND a new, non-repeating cursor. A final page that fills
            # the cap but hands back no cursor is end-of-data, not truncation.
            state["more"] = bool(records) and bool(next_token) and next_token != cursor
            return records, next_token

        for page in paginator.pages(fetch):
            yield from page

        if state["pages"] >= max_pages and state["more"]:
            self.logger.warning(
                f"Databricks {label} truncated at the {max_pages}-page cap — "
                f"more pages were available and the remaining records were "
                f"not collected."
            )

    def _collect_resource(self, label, max_pages, url, result_key,
                          normalize, extra_params=None, use_has_more=False):
        self.logger.info(f"Collecting Databricks {label}")
        for raw in self._paginate(label, max_pages, url, result_key,
                                  extra_params, use_has_more):
            yield normalize(raw)

    def _normalize_cluster(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "resource_type": "cluster",
            "cluster_id": raw.get("cluster_id"),
            "cluster_name": raw.get("cluster_name"),
            "state": raw.get("state"),
            "state_message": raw.get("state_message"),
            "spark_version": raw.get("spark_version"),
            "node_type_id": raw.get("node_type_id"),
            "driver_node_type_id": raw.get("driver_node_type_id"),
            "num_workers": raw.get("num_workers"),
            "cluster_cores": raw.get("cluster_cores"),
            "cluster_memory_mb": raw.get("cluster_memory_mb"),
            "creator_user_name": raw.get("creator_user_name"),
            "cluster_source": raw.get("cluster_source"),
            "start_time": raw.get("start_time"),
            "terminated_time": raw.get("terminated_time"),
            "last_state_loss_time": raw.get("last_state_loss_time"),
            "autotermination_minutes": raw.get("autotermination_minutes"),
            "enable_elastic_disk": raw.get("enable_elastic_disk"),
            "data_security_mode": raw.get("data_security_mode"),
            "runtime_engine": raw.get("runtime_engine"),
            "is_pinned": raw.get("is_pinned"),
            "instance_pool_id": raw.get("instance_pool_id"),
        }

        # Flatten autoscale
        autoscale = raw.get("autoscale")
        if isinstance(autoscale, dict):
            record["autoscale_min_workers"] = autoscale.get("min_workers")
            record["autoscale_max_workers"] = autoscale.get("max_workers")

        # Flatten tags dicts to "key=value, ..." strings
        for tag_field in ("custom_tags", "default_tags"):
            tags = raw.get(tag_field)
            if isinstance(tags, dict):
                record[tag_field] = ", ".join(f"{k}={v}" for k, v in tags.items()) if tags else ""
            else:
                record[tag_field] = tags

        return record

    # ------------------------------------------------------------------
    # Normalizers
    # ------------------------------------------------------------------

    def _normalize_job(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        settings = raw.get("settings") or {}
        schedule = settings.get("schedule") or {}
        return {
            "resource_type": "job",
            "job_id": raw.get("job_id"),
            "job_name": settings.get("name"),
            "description": settings.get("description"),
            "created_time": raw.get("created_time"),
            "creator_user_name": raw.get("creator_user_name"),
            "run_as_user_name": raw.get("run_as_user_name"),
            "format": settings.get("format"),
            "max_concurrent_runs": settings.get("max_concurrent_runs"),
            "schedule_cron": schedule.get("quartz_cron_expression"),
            "schedule_timezone": schedule.get("timezone_id"),
        }

    def _normalize_warehouse(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "resource_type": "sql_warehouse",
            "warehouse_id": raw.get("id"),
            "warehouse_name": raw.get("name"),
            "cluster_size": raw.get("cluster_size"),
            "state": raw.get("state"),
            "creator_name": raw.get("creator_name"),
            "warehouse_type": raw.get("warehouse_type"),
            "enable_photon": raw.get("enable_photon"),
            "enable_serverless_compute": raw.get("enable_serverless_compute"),
            "auto_stop_mins": raw.get("auto_stop_mins"),
            "min_num_clusters": raw.get("min_num_clusters"),
            "max_num_clusters": raw.get("max_num_clusters"),
            "num_clusters": raw.get("num_clusters"),
            "num_active_sessions": raw.get("num_active_sessions"),
            "spot_instance_policy": raw.get("spot_instance_policy"),
        }

        # Flatten health
        health = raw.get("health")
        if isinstance(health, dict):
            record["health_status"] = health.get("status")
            record["health_message"] = health.get("message")

        # Flatten tags: {"custom_tags": [{"key": "K", "value": "V"}, ...]}
        tags = raw.get("tags")
        if isinstance(tags, dict):
            custom_tags = tags.get("custom_tags", [])
            if isinstance(custom_tags, list):
                record["tags"] = ", ".join(
                    f"{t['key']}={t['value']}" for t in custom_tags if "key" in t and "value" in t
                )
            else:
                record["tags"] = None
        else:
            record["tags"] = None

        return record

    # ------------------------------------------------------------------
    # Unity Catalog (optional)
    # ------------------------------------------------------------------

    def _collect_unity_catalog(self) -> Iterator[Dict[str, Any]]:
        self.logger.info("Collecting Unity Catalog assets")

        uc_url = "/api/2.1/unity-catalog"
        try:
            catalogs = list(self._fetch_all_uc(
                f"{uc_url}/catalogs", "catalogs",
                max_pages=self._max_pages["catalogs"],
            ))
        except Exception as e:
            if self._is_credential_rejection(e):
                raise
            self.logger.warning(f"Unity Catalog not available, skipping: {e}")
            return

        for catalog_raw in catalogs:
            yield self._normalize_catalog(catalog_raw)
            catalog_name = catalog_raw.get("name")
            if not catalog_name:
                continue

            try:
                schemas = list(self._fetch_all_uc(
                    f"{uc_url}/schemas", "schemas",
                    max_pages=self._max_pages["schemas"],
                    extra_params={"catalog_name": catalog_name},
                    scope=f"catalog '{catalog_name}'",
                ))
            except Exception as e:
                if self._is_credential_rejection(e):
                    raise
                self.logger.warning(f"Cannot list schemas for catalog '{catalog_name}': {e}")
                continue

            for schema_raw in schemas:
                yield self._normalize_schema(schema_raw)
                schema_name = schema_raw.get("name")
                if not schema_name:
                    continue

                try:
                    for table_raw in self._fetch_all_uc(
                        f"{uc_url}/tables", "tables",
                        max_pages=self._max_pages["tables"],
                        extra_params={
                            "catalog_name": catalog_name,
                            "schema_name": schema_name,
                            "max_results": self.page_size,
                        },
                        scope=f"'{catalog_name}.{schema_name}'",
                    ):
                        yield self._normalize_table(table_raw)
                except Exception as e:
                    if self._is_credential_rejection(e):
                        raise
                    self.logger.warning(
                        f"Cannot list tables for '{catalog_name}.{schema_name}': {e}"
                    )

    def _fetch_all_uc(self, url, result_key, max_pages,
                      extra_params=None, scope="the workspace") -> Iterator[dict]:
        yield from self._paginate(
            f"Unity Catalog {result_key} in {scope}",
            max_pages, url, result_key, extra_params,
        )

    def _normalize_catalog(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "resource_type": "catalog",
            "catalog_name": raw.get("name"),
            "catalog_type": raw.get("catalog_type"),
            "comment": raw.get("comment"),
            "owner": raw.get("owner"),
            "created_at": raw.get("created_at"),
            "created_by": raw.get("created_by"),
            "metastore_id": raw.get("metastore_id"),
            "isolation_mode": raw.get("isolation_mode"),
            "full_name": raw.get("full_name"),
        }

    def _normalize_schema(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "resource_type": "schema",
            "schema_name": raw.get("name"),
            "schema_full_name": raw.get("full_name"),
            "catalog_name": raw.get("catalog_name"),
            "catalog_type": raw.get("catalog_type"),
            "comment": raw.get("comment"),
            "owner": raw.get("owner"),
            "created_at": raw.get("created_at"),
            "created_by": raw.get("created_by"),
            "schema_id": raw.get("schema_id"),
        }

    def _normalize_table(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "resource_type": "table",
            "table_name": raw.get("name"),
            "table_full_name": raw.get("full_name"),
            "catalog_name": raw.get("catalog_name"),
            "schema_name": raw.get("schema_name"),
            "table_type": raw.get("table_type"),
            "data_source_format": raw.get("data_source_format"),
            "owner": raw.get("owner"),
            "created_at": raw.get("created_at"),
            "created_by": raw.get("created_by"),
            "table_id": raw.get("table_id"),
            "storage_location": raw.get("storage_location"),
        }

    def get_schema(self) -> Dict[str, Any]:
        if self.mode == "sql":
            return self._get_sql_schema()
        # Every key any normalizer above can emit must appear here: a field
        # missing from this list is collected into the raw record and then
        # invisible to the wizard's field mapping, so it can never be mapped
        # onto an asset. tests/unit/connectors/test_databricks_connector.py
        # enforces the union mechanically.
        fields = [
            # Common
            {"name": "resource_type", "path": "resource_type", "type": "string", "description": "Resource type (cluster, job, sql_warehouse, catalog, schema, table)", "required": True},
            # Cluster fields
            {"name": "cluster_id", "path": "cluster_id", "type": "string", "description": "Cluster unique identifier"},
            {"name": "cluster_name", "path": "cluster_name", "type": "string", "description": "Cluster name"},
            {"name": "state", "path": "state", "type": "string", "description": "Resource state (RUNNING, TERMINATED, STOPPED, etc.)"},
            {"name": "state_message", "path": "state_message", "type": "string", "description": "Why the resource is in its current state (pending reason, termination reason)"},
            {"name": "spark_version", "path": "spark_version", "type": "string", "description": "Spark runtime version"},
            {"name": "node_type_id", "path": "node_type_id", "type": "string", "description": "Instance type"},
            {"name": "driver_node_type_id", "path": "driver_node_type_id", "type": "string", "description": "Driver node instance type"},
            {"name": "num_workers", "path": "num_workers", "type": "number", "description": "Number of worker nodes (fixed-size clusters)"},
            {"name": "autoscale_min_workers", "path": "autoscale_min_workers", "type": "number", "description": "Minimum worker nodes for an autoscaling cluster"},
            {"name": "autoscale_max_workers", "path": "autoscale_max_workers", "type": "number", "description": "Maximum worker nodes for an autoscaling cluster"},
            {"name": "cluster_cores", "path": "cluster_cores", "type": "number", "description": "Total CPU cores"},
            {"name": "cluster_memory_mb", "path": "cluster_memory_mb", "type": "number", "description": "Total memory in MB"},
            {"name": "creator_user_name", "path": "creator_user_name", "type": "string", "description": "Creator username"},
            {"name": "cluster_source", "path": "cluster_source", "type": "string", "description": "What created the cluster (UI, JOB, API)"},
            {"name": "start_time", "path": "start_time", "type": "number", "description": "Cluster start time (epoch ms)"},
            {"name": "terminated_time", "path": "terminated_time", "type": "number", "description": "Cluster termination time (epoch ms)"},
            {"name": "last_state_loss_time", "path": "last_state_loss_time", "type": "number", "description": "Last time the cluster lost its state (epoch ms)"},
            {"name": "autotermination_minutes", "path": "autotermination_minutes", "type": "number", "description": "Idle minutes before automatic termination (0 = disabled)"},
            {"name": "enable_elastic_disk", "path": "enable_elastic_disk", "type": "boolean", "description": "Elastic disk (autoscaling local storage) enabled"},
            {"name": "data_security_mode", "path": "data_security_mode", "type": "string", "description": "Data security mode"},
            {"name": "runtime_engine", "path": "runtime_engine", "type": "string", "description": "Runtime engine (STANDARD, PHOTON)"},
            {"name": "is_pinned", "path": "is_pinned", "type": "boolean", "description": "Cluster is pinned (exempt from automatic cleanup)"},
            {"name": "instance_pool_id", "path": "instance_pool_id", "type": "string", "description": "Instance pool the cluster draws nodes from"},
            {"name": "custom_tags", "path": "custom_tags", "type": "string", "description": "User-defined cluster tags, flattened to 'key=value, ...'"},
            {"name": "default_tags", "path": "default_tags", "type": "string", "description": "Databricks-managed cluster tags, flattened to 'key=value, ...'"},
            # Job fields
            {"name": "job_id", "path": "job_id", "type": "number", "description": "Job unique identifier"},
            {"name": "job_name", "path": "job_name", "type": "string", "description": "Job name"},
            {"name": "description", "path": "description", "type": "string", "description": "Job description"},
            {"name": "created_time", "path": "created_time", "type": "number", "description": "Job creation time (epoch ms)"},
            {"name": "run_as_user_name", "path": "run_as_user_name", "type": "string", "description": "Identity the job runs as (may differ from the creator)"},
            {"name": "format", "path": "format", "type": "string", "description": "Job format (SINGLE_TASK, MULTI_TASK)"},
            {"name": "max_concurrent_runs", "path": "max_concurrent_runs", "type": "number", "description": "Maximum concurrent runs allowed for the job"},
            {"name": "schedule_cron", "path": "schedule_cron", "type": "string", "description": "Job schedule (Quartz cron)"},
            {"name": "schedule_timezone", "path": "schedule_timezone", "type": "string", "description": "Timezone the job schedule is evaluated in"},
            # SQL Warehouse fields
            {"name": "warehouse_id", "path": "warehouse_id", "type": "string", "description": "SQL warehouse ID"},
            {"name": "warehouse_name", "path": "warehouse_name", "type": "string", "description": "SQL warehouse name"},
            {"name": "cluster_size", "path": "cluster_size", "type": "string", "description": "Warehouse cluster size"},
            {"name": "creator_name", "path": "creator_name", "type": "string", "description": "SQL warehouse creator"},
            {"name": "warehouse_type", "path": "warehouse_type", "type": "string", "description": "Warehouse type (CLASSIC, PRO)"},
            {"name": "enable_photon", "path": "enable_photon", "type": "boolean", "description": "Photon acceleration enabled"},
            {"name": "enable_serverless_compute", "path": "enable_serverless_compute", "type": "boolean", "description": "Serverless compute enabled"},
            {"name": "auto_stop_mins", "path": "auto_stop_mins", "type": "number", "description": "Idle minutes before the warehouse stops (0 = never)"},
            {"name": "min_num_clusters", "path": "min_num_clusters", "type": "number", "description": "Minimum clusters in the warehouse scaling range"},
            {"name": "max_num_clusters", "path": "max_num_clusters", "type": "number", "description": "Maximum clusters in the warehouse scaling range"},
            {"name": "num_clusters", "path": "num_clusters", "type": "number", "description": "Clusters currently allocated to the warehouse"},
            {"name": "num_active_sessions", "path": "num_active_sessions", "type": "number", "description": "Active sessions on the warehouse"},
            {"name": "spot_instance_policy", "path": "spot_instance_policy", "type": "string", "description": "Spot instance policy (COST_OPTIMIZED, RELIABILITY_OPTIMIZED)"},
            {"name": "health_status", "path": "health_status", "type": "string", "description": "Warehouse health status (HEALTHY, DEGRADED, FAILED)"},
            {"name": "health_message", "path": "health_message", "type": "string", "description": "Warehouse health detail"},
            {"name": "tags", "path": "tags", "type": "string", "description": "SQL warehouse tags, flattened to 'key=value, ...'"},
            # Unity Catalog fields
            {"name": "catalog_name", "path": "catalog_name", "type": "string", "description": "Unity Catalog name"},
            {"name": "catalog_type", "path": "catalog_type", "type": "string", "description": "Catalog type (MANAGED_CATALOG, DELTASHARING_CATALOG, etc.)"},
            {"name": "comment", "path": "comment", "type": "string", "description": "Free-text comment on the catalog or schema"},
            {"name": "owner", "path": "owner", "type": "string", "description": "Owning principal of the catalog, schema or table"},
            {"name": "created_at", "path": "created_at", "type": "number", "description": "Unity Catalog object creation time (epoch ms)"},
            {"name": "created_by", "path": "created_by", "type": "string", "description": "Who created the Unity Catalog object"},
            {"name": "metastore_id", "path": "metastore_id", "type": "string", "description": "Metastore the catalog belongs to"},
            {"name": "isolation_mode", "path": "isolation_mode", "type": "string", "description": "Catalog isolation mode (OPEN, ISOLATED)"},
            {"name": "full_name", "path": "full_name", "type": "string", "description": "Fully qualified catalog name"},
            {"name": "schema_name", "path": "schema_name", "type": "string", "description": "Schema name"},
            {"name": "schema_full_name", "path": "schema_full_name", "type": "string", "description": "Fully qualified schema name (catalog.schema)"},
            {"name": "schema_id", "path": "schema_id", "type": "string", "description": "Schema unique identifier"},
            {"name": "table_name", "path": "table_name", "type": "string", "description": "Table name"},
            {"name": "table_full_name", "path": "table_full_name", "type": "string", "description": "Fully qualified table name (catalog.schema.table)"},
            {"name": "table_type", "path": "table_type", "type": "string", "description": "Table type (MANAGED, EXTERNAL, VIEW)"},
            {"name": "data_source_format", "path": "data_source_format", "type": "string", "description": "Data format (DELTA, PARQUET, etc.)"},
            {"name": "table_id", "path": "table_id", "type": "string", "description": "Table unique identifier"},
            {"name": "storage_location", "path": "storage_location", "type": "string", "description": "Backing storage URL for an external table"},
        ]
        return {
            "source": self.integration_type,
            "version": "1.1",
            "fields": fields,
            "sample_record_count": 3,
        }

    def _get_sql_schema(self) -> Dict[str, Any]:
        # SQL mode has no discoverable field list — the fields are whatever
        # the configured statement returns. This is the via-SIEM v1 contract
        # (docs/features/integrations/via-siem.md): the pre-normalized columns
        # the query is expected to produce, offered as a hint for the wizard's
        # field mapping. Same shape as the Splunk/Log Analytics transports.
        fields = [
            {"name": "hostname", "path": "hostname", "type": "string", "description": "Hostname — primary identifier when SN/MAC absent"},
            {"name": "fqdn", "path": "fqdn", "type": "string", "description": "Fully qualified domain name"},
            {"name": "ip", "path": "ip", "type": "string", "description": "IP address — one row per IP for multi-IP assets"},
            {"name": "mac", "path": "mac", "type": "string", "description": "MAC address (lowercase, colon-separated preferred)"},
            {"name": "sn", "path": "sn", "type": "string", "description": "Serial number — strongest unique identifier"},
            {"name": "os", "path": "os", "type": "string", "description": "Operating system (free-form; the pipeline normalizes)"},
            {"name": "manufacturer", "path": "manufacturer", "type": "string", "description": "Hardware manufacturer"},
            {"name": "model", "path": "model", "type": "string", "description": "Hardware model"},
            {"name": "vendor_category", "path": "vendor_category", "type": "string", "description": "Vendor's device category (e.g. Firewall, Switch, Endpoint)"},
            {"name": "first_time", "path": "first_time", "type": "datetime", "description": "When the source first observed this asset"},
            {"name": "last_time", "path": "last_time", "type": "datetime", "description": "Last observation — defaults to the query window end if absent"},
        ]
        return {
            "source": self.integration_type,
            "version": "1.0",
            "fields": fields,
            "sample_record_count": 3,
            "notes": (
                "Fields returned depend on your SQL statement. The query "
                "returns pre-normalized asset rows whose column names match "
                "the via-SIEM schema; extra columns are stored verbatim "
                "under extended_fields."
            ),
        }
