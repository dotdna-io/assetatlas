# SPDX-License-Identifier: AGPL-3.0-or-later
"""Custom HTTP API Connector.

Config-driven generic connector. Users configure auth, pagination, request
shape, records-root JSONPath, and per-field JSONPath bindings entirely from
the wizard — no Python code required per integration instance.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests as _requests

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import (
    APIKeyAuth,
    AuthStrategy,
    ConfigSchema,
    CursorPaginator,
    LinkPaginator,
    NoAuth,
    NoPaginator,
    OffsetPaginator,
    PagePaginator,
    get_auth_strategy,
)
from src.modules.integrations.shared.json_path import (
    _compile as _compile_jsonpath,
    validate as validate_jsonpath,
)
from src.modules.integrations.shared.rfc5988_link import parse_next_link


_VALID_AUTH_TYPES = {"none", "basic", "bearer", "api_key", "oauth2", "login_password"}
_VALID_PAGINATION_TYPES = {"none", "offset", "page", "cursor", "link"}
_VALID_METHODS = {"GET", "POST"}


@ConnectorRegistry.register
class CustomHttpConnector(BaseConnector):
    """Generic HTTP API connector configured entirely from the UI."""

    integration_type = "custom_http"
    display_name = "Custom HTTP API"
    category = "Custom"
    description = "Integrate your API-based data sources (no code required)"
    logo_url = "/logos/custom-http.svg"

    # ---- Construction --------------------------------------------------------

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # Cache static request parameters once instead of re-reading
        # self.config on every page fetch / per-record extraction.
        self._method = (config.get("method") or "GET").upper()
        self._url = config.get("url", "")
        self._static_params = dict(config.get("static_query_params") or {})
        self._static_headers = dict(config.get("headers") or {})
        self._static_body = dict(config.get("static_body") or {})
        self._timeout = int(config.get("request_timeout_seconds", 30))
        self._pagination_type = (config.get("pagination_type") or "none").lower()
        # Default cap of 5000 pages × the default page size of 100 = 500,000
        # records. The page size stays at 100: this connector speaks to
        # arbitrary third-party APIs, and 100 is the size virtually all of
        # them accept — the headroom for large estates comes from the page
        # cap, which only binds when the API actually has more data.
        self._max_pages = int(config.get("max_pages", 5000))
        self._page_size = int(config.get("page_size") or 100)
        self._offset_param = config.get("offset_param", "offset")
        self._limit_param = config.get("limit_param", "limit")
        self._page_param = config.get("page_param", "page")
        self._perpage_param = config.get("perpage_param", "perPage")
        self._start_page = int(config.get("start_page") or 1)
        self._cursor_request_param = config.get("cursor_request_param", "cursor")

        # Pre-compile JSONPath expressions so the per-record hot loop is a
        # flat list traversal — no string hashing, no LRU dispatch.
        # Tolerant of invalid paths: validate_config() surfaces those
        # before the connector is invoked for ingestion.
        # Empty records_root → auto-detect the records list at fetch time.
        records_root_raw = (config.get("records_root") or "").strip()
        self._records_root_compiled = (
            _safe_compile(records_root_raw) if records_root_raw else None
        )
        # When records_root is empty we auto-detect the path on the first
        # successful page and reuse it for every subsequent page — saves
        # the priority-key scan + the array-key listing on every page.
        self._auto_detected_root_compiled: Optional[Any] = None
        self._total_path_compiled = _safe_compile(config.get("total_path") or "$.total")
        self._cursor_response_path_compiled = _safe_compile(
            config.get("cursor_response_path") or "$.next"
        )
        self._passthrough = bool(config.get("passthrough"))
        self._compiled_bindings: List[Tuple[Any, str]] = []
        for b in (config.get("field_bindings") or []):
            path = b.get("source_path") or ""
            name = b.get("source_name") or ""
            if not path or not name:
                continue
            compiled = _safe_compile(path)
            if compiled is not None:
                self._compiled_bindings.append((compiled, name))

    # ---- Connector framework hooks -------------------------------------------

    def _build_auth(self) -> Optional[AuthStrategy]:
        return _build_auth_for(self.config, self.logger)

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        # Minimal stub — the real form is CustomHttpConfigStep.tsx, which
        # writes the full blob into config. The wizard's generic
        # connector-form fallback uses this only if the custom step is not
        # rendered (e.g. when reviewing a saved config from a script).
        # max_pages is declared here as well so the cap stays visible and
        # editable on that fallback form; the key name is load-bearing —
        # customer configs already persist it.
        return (
            ConfigSchema()
            .add_url("url", "Endpoint URL")
            .add_number(
                "max_pages", "Max Pagination Pages",
                default=5000,
                min_val=1,
                max_val=100000,
                description=(
                    "Safety cap on API pages fetched per collection run — it stops a "
                    "pagination loop that never terminates. At the default page size "
                    "of 100, the default cap of 5,000 pages collects up to 500,000 records."
                ),
            )
            .build()
        )

    # ---- Validation ----------------------------------------------------------

    def validate_config(self) -> Dict[str, Any]:
        errors: List[str] = []

        method = (self.config.get("method") or "GET").upper()
        if method not in _VALID_METHODS:
            errors.append(f"Unsupported method '{method}' (allowed: GET, POST)")

        url = self.config.get("url") or ""
        if not url:
            errors.append("url is required")
        elif not (url.startswith("http://") or url.startswith("https://")):
            errors.append("url must start with http:// or https://")

        auth_type = (self.config.get("auth_type") or "none").lower()
        if auth_type not in _VALID_AUTH_TYPES:
            errors.append(
                f"Unsupported auth_type '{auth_type}' "
                f"(allowed: {', '.join(sorted(_VALID_AUTH_TYPES))})"
            )
        else:
            errors.extend(_validate_auth_fields(auth_type, self.config))

        pagination_type = (self.config.get("pagination_type") or "none").lower()
        if pagination_type not in _VALID_PAGINATION_TYPES:
            errors.append(
                f"Unsupported pagination_type '{pagination_type}' "
                f"(allowed: {', '.join(sorted(_VALID_PAGINATION_TYPES))})"
            )
        else:
            errors.extend(_validate_pagination_fields(pagination_type, self.config))

        records_root = (self.config.get("records_root") or "").strip()
        if records_root:
            err = validate_jsonpath(records_root)
            if err:
                errors.append(f"records_root is not a valid JSONPath: {err}")

        bindings = self.config.get("field_bindings") or []
        passthrough = bool(self.config.get("passthrough"))
        if not bindings and not passthrough:
            errors.append(
                "at least one field binding is required "
                "(or enable passthrough to use the full record)"
            )
        if bindings:
            seen: set = set()
            for i, b in enumerate(bindings):
                src_path = (b.get("source_path") or "").strip()
                src_name = (b.get("source_name") or "").strip()
                if not src_path:
                    errors.append(f"field_bindings[{i}].source_path is required")
                else:
                    err = validate_jsonpath(src_path)
                    if err:
                        errors.append(
                            f"field_bindings[{i}].source_path is not a valid JSONPath: {err}"
                        )
                if not src_name:
                    errors.append(f"field_bindings[{i}].source_name is required")
                elif src_name in seen:
                    errors.append(
                        f"field_bindings source_name values must be unique "
                        f"('{src_name}' is duplicate)"
                    )
                else:
                    seen.add(src_name)

        return {"valid": len(errors) == 0, "errors": errors}

    # ---- Runtime helpers ----------------------------------------------------

    def _apply_bindings(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve each pre-compiled binding against `raw`. Missing → None,
        single match → scalar, multi-match → list.

        In passthrough mode, return the record verbatim — the field-mapping
        step downstream walks nested keys via extractFieldsRecursive.
        """
        if self._passthrough:
            return dict(raw) if isinstance(raw, dict) else {"value": raw}
        out: Dict[str, Any] = {}
        for compiled, name in self._compiled_bindings:
            matches = compiled.find(raw)
            if not matches:
                out[name] = None
            elif len(matches) == 1:
                out[name] = matches[0].value
            else:
                out[name] = [m.value for m in matches]
        return out

    def _build_paginator(self):
        if self._pagination_type == "offset":
            return OffsetPaginator(page_size=self._page_size, max_pages=self._max_pages)
        if self._pagination_type == "page":
            return PagePaginator(
                page_size=self._page_size,
                start_page=self._start_page,
                max_pages=self._max_pages,
            )
        if self._pagination_type == "cursor":
            return CursorPaginator(max_pages=self._max_pages)
        if self._pagination_type == "link":
            return LinkPaginator(max_pages=self._max_pages)
        return NoPaginator()

    # --- HTTP call closures: one per paginator shape -------------------------

    def _do_request(self, *, params=None, body_overlay=None, url=None):
        """Make one HTTP call through self.auth, return the Response object.

        - params: extra query params to merge into static_query_params
        - body_overlay: extra keys to merge into a copy of static_body (POST only)
        - url: override URL (used for link pagination next-URL fetches)
        """
        target = url or self._url

        if params:
            merged_params = dict(self._static_params)
            merged_params.update({k: v for k, v in params.items() if v is not None})
        else:
            merged_params = self._static_params

        kwargs: Dict[str, Any] = {
            "headers": self._static_headers,
            "timeout": self._timeout,
        }
        if merged_params:
            kwargs["params"] = merged_params

        if self._method == "POST":
            if body_overlay:
                body = dict(self._static_body)
                body.update(body_overlay)
            else:
                body = self._static_body
            kwargs["json"] = body

        response = self.auth.request(target, method=self._method, **kwargs)
        response.raise_for_status()
        return response

    @staticmethod
    def auto_detect_records_root(
        body: Any,
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Find the records list when the user didn't specify a JSONPath.

        Strategy:
            - bare list           → records = body, path = '$'
            - dict with a known key (items, data, results, value, records,
              rows, entries) whose value is a NON-EMPTY list → use it
            - dict with exactly one NON-EMPTY array-valued top-level key
              → use it
            - dict with priority/single-key match but the array is empty
              → use it (legitimately empty page; paginator will stop)
            - anything else       → (None, [])

        Empty arrays only "win" when there's nothing better. Otherwise an
        API response like {"items": [], "data": [...]} would mis-select
        `items` and silently lose records.

        Returns (jsonpath, records). Path is informational — the wizard
        surfaces it back to the user so they can see what was picked.
        """
        if isinstance(body, list):
            return "$", [r for r in body if isinstance(r, dict)]
        if not isinstance(body, dict):
            return None, []
        for key in _AUTO_RECORDS_KEYS:
            v = body.get(key)
            if isinstance(v, list) and v:
                return f"$.{key}", [r for r in v if isinstance(r, dict)]
        nonempty_array_keys = [k for k, v in body.items() if isinstance(v, list) and v]
        if len(nonempty_array_keys) == 1:
            k = nonempty_array_keys[0]
            return f"$.{k}", [r for r in body[k] if isinstance(r, dict)]
        for key in _AUTO_RECORDS_KEYS:
            v = body.get(key)
            if isinstance(v, list):
                return f"$.{key}", []
        array_keys = [k for k, v in body.items() if isinstance(v, list)]
        if len(array_keys) == 1:
            return f"$.{array_keys[0]}", []
        return None, []

    def _extract_records(self, body: Any) -> List[Dict[str, Any]]:
        """Apply pre-compiled records_root; wrap a single object in a list.

        If records_root was not configured, auto-detect the records list
        from the response shape (see auto_detect_records_root). The path
        is detected on the first non-empty page and reused for the rest
        of the pagination loop.
        """
        compiled = self._records_root_compiled or self._auto_detected_root_compiled
        if compiled is None:
            path, records = self.auto_detect_records_root(body)
            if path is not None and records:
                # Cache the path so subsequent pages skip the scan.
                self._auto_detected_root_compiled = _safe_compile(path)
            return records
        matches = compiled.find(body)
        if not matches:
            return []
        result = matches[0].value
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []

    # --- Per-paginator-shape fetchers ----------------------------------------

    def _fetch_page_simple(self) -> List[Dict[str, Any]]:
        """For NoPaginator: one call, return records."""
        response = self._do_request()
        return self._extract_records(response.json())

    def _fetch_paged(self, page_params: Dict[str, Any]):
        """One paged fetch with the given param overlay. Used by both offset
        and page paginators — only the param keys differ. Returns
        (records, total_or_none)."""
        if self._method == "POST":
            response = self._do_request(body_overlay=page_params)
        else:
            response = self._do_request(params=page_params)
        body = response.json()
        records = self._extract_records(body)
        total = None
        if self._total_path_compiled is not None:
            total_matches = self._total_path_compiled.find(body)
            if total_matches:
                total = total_matches[0].value
        return records, total

    def _fetch_page_offset(self, offset: int, limit: int):
        """For OffsetPaginator: returns (records, total_or_none)."""
        return self._fetch_paged(
            {self._offset_param: offset, self._limit_param: limit}
        )

    def _fetch_page_page(self, page: int, page_size: int):
        """For PagePaginator: returns (records, total_or_none)."""
        return self._fetch_paged(
            {self._page_param: page, self._perpage_param: page_size}
        )

    def _fetch_page_cursor(self, cursor):
        """For CursorPaginator: returns (records, next_cursor_or_none)."""
        if cursor is not None:
            page_params = {self._cursor_request_param: cursor}
            if self._method == "POST":
                response = self._do_request(body_overlay=page_params)
            else:
                response = self._do_request(params=page_params)
        else:
            response = self._do_request()
        body = response.json()
        records = self._extract_records(body)
        next_cursor = None
        if self._cursor_response_path_compiled is not None:
            cursor_matches = self._cursor_response_path_compiled.find(body)
            if cursor_matches:
                next_cursor = cursor_matches[0].value
        return records, next_cursor

    def _fetch_page_link(self, url: Optional[str]):
        """For LinkPaginator: returns (records, next_url_or_none).

        First call uses self._url; subsequent calls use the prior page's
        rel=next link verbatim (already a fully-qualified URL per RFC 5988).
        """
        response = self._do_request(url=url) if url else self._do_request()
        body = response.json()
        records = self._extract_records(body)
        next_url = parse_next_link(response.headers.get("Link"))
        return records, next_url

    # ---- Required-by-framework methods --------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        """Source fields exposed to FieldMappingStep — one per binding."""
        fields = [
            {
                "name": b.get("source_name", ""),
                "path": b.get("source_name", ""),
                "type": "string",
            }
            for b in (self.config.get("field_bindings") or [])
            if b.get("source_name")
        ]
        return {
            "source": self.integration_type,
            "version": "1.0",
            "fields": fields,
        }

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        paginator = self._build_paginator()

        if self._pagination_type == "offset":
            pages = paginator.pages(self._fetch_page_offset)
        elif self._pagination_type == "page":
            pages = paginator.pages(self._fetch_page_page)
        elif self._pagination_type == "cursor":
            pages = paginator.pages(self._fetch_page_cursor)
        elif self._pagination_type == "link":
            pages = paginator.pages(self._fetch_page_link)
        else:
            pages = paginator.pages(self._fetch_page_simple)

        for records in pages:
            for raw in records:
                yield self._apply_bindings(raw)

        # Static label: this string is persisted to integrations.last_error,
        # and a configured URL can carry an API key in its query string.
        self.warn_if_truncated(paginator, "Custom HTTP collection")

    def test_connection(self) -> Dict[str, Any]:
        """Single HTTP call to verify auth + endpoint reachability."""
        try:
            result = self.run_single_call()
            return {
                "success": True,
                "message": f"Connected: HTTP {result['status_code']}",
                "status_code": result["status_code"],
                "elapsed_ms": result["elapsed_ms"],
            }
        except _requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            return {
                "success": False,
                "message": f"HTTP {status}: {e}",
                "status_code": status,
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

    def run_single_call(self) -> Dict[str, Any]:
        """Used by /test endpoint and test_connection. Returns shape:

            {
                'status_code': int,
                'elapsed_ms': float,
                'response_headers': dict,
                'body': dict | {'_raw': str},
                'body_size_bytes': int,
            }
        """
        start = time.time()
        response = self._do_request()
        elapsed_ms = (time.time() - start) * 1000.0

        try:
            body = response.json()
        except ValueError:
            body = {"_raw": response.text[:51200]}

        response_headers = _redact_response_headers(response.headers)

        return {
            "status_code": response.status_code,
            "elapsed_ms": round(elapsed_ms, 2),
            "response_headers": response_headers,
            "body": body,
            "body_size_bytes": len(response.content or b""),
        }


# =============================================================================
# Helpers (module-level so they're easy to unit-test in isolation)
# =============================================================================


_AUTO_RECORDS_KEYS: Tuple[str, ...] = (
    "items", "data", "results", "value", "records", "rows", "entries",
)


def _safe_compile(path: str):
    """Compile a JSONPath, returning None on failure.

    Used during connector construction to populate caches without raising
    when a user has supplied an invalid path — validate_config() surfaces
    the actual error before the connector is invoked for ingestion.
    """
    try:
        return _compile_jsonpath(path)
    except Exception:
        return None


def _validate_auth_fields(auth_type: str, config: Dict[str, Any]) -> List[str]:
    """Per-auth_type required-field validation. Returns list of error messages."""
    errors: List[str] = []
    if auth_type == "basic":
        if not config.get("username"):
            errors.append("basic auth requires 'username'")
        if not config.get("password"):
            errors.append("basic auth requires 'password'")
    elif auth_type == "bearer":
        if not config.get("bearer_token"):
            errors.append("bearer auth requires 'bearer_token'")
    elif auth_type == "api_key":
        if not config.get("api_key"):
            errors.append("api_key auth requires 'api_key'")
        loc = (config.get("api_key_location") or "header").lower()
        if loc not in ("header", "query"):
            errors.append("api_key_location must be 'header' or 'query'")
    elif auth_type == "oauth2":
        if not config.get("token_url"):
            errors.append("oauth2 auth requires 'token_url'")
        if not config.get("client_id"):
            errors.append("oauth2 auth requires 'client_id'")
        if not config.get("client_secret"):
            errors.append("oauth2 auth requires 'client_secret'")
    elif auth_type == "login_password":
        if not config.get("login_url"):
            errors.append("login_password auth requires 'login_url'")
        if not config.get("identity"):
            errors.append("login_password auth requires 'identity'")
        if not config.get("password"):
            errors.append("login_password auth requires 'password'")
        token_path = config.get("token_path") or "$.token"
        err = validate_jsonpath(token_path)
        if err:
            errors.append(f"token_path is not a valid JSONPath: {err}")
    # 'none' has no required fields
    return errors


def _validate_pagination_fields(pagination_type: str, config: Dict[str, Any]) -> List[str]:
    """Per-pagination_type required-field validation."""
    errors: List[str] = []
    if pagination_type == "offset":
        page_size = config.get("page_size")
        try:
            page_size_i = int(page_size or 0)
        except (TypeError, ValueError):
            page_size_i = 0
        if page_size_i <= 0:
            errors.append("offset pagination requires page_size > 0")
        if not config.get("offset_param"):
            errors.append("offset pagination requires 'offset_param'")
        if not config.get("limit_param"):
            errors.append("offset pagination requires 'limit_param'")
    elif pagination_type == "page":
        page_size = config.get("page_size")
        try:
            page_size_i = int(page_size or 0)
        except (TypeError, ValueError):
            page_size_i = 0
        if page_size_i <= 0:
            errors.append("page pagination requires page_size > 0")
        if not config.get("page_param"):
            errors.append("page pagination requires 'page_param'")
        if not config.get("perpage_param"):
            errors.append("page pagination requires 'perpage_param'")
        if "start_page" in config:
            try:
                int(config["start_page"])
            except (TypeError, ValueError):
                errors.append("start_page must be an integer")
    elif pagination_type == "cursor":
        if not config.get("cursor_request_param"):
            errors.append("cursor pagination requires 'cursor_request_param'")
        if not config.get("cursor_response_path"):
            errors.append("cursor pagination requires 'cursor_response_path'")
        else:
            err = validate_jsonpath(config["cursor_response_path"])
            if err:
                errors.append(f"cursor_response_path is not a valid JSONPath: {err}")
    # 'none' and 'link' have no required fields
    return errors


_REDACTED_HEADERS = {
    "set-cookie", "authorization", "proxy-authorization",
}
_REDACTED_PATTERNS = ("token", "secret", "key", "password")


def _redact_response_headers(headers) -> Dict[str, str]:
    """Drop sensitive response headers before returning to the wizard."""
    out: Dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower in _REDACTED_HEADERS:
            continue
        if any(p in lower for p in _REDACTED_PATTERNS):
            continue
        out[name] = value
    return out


def _build_auth_for(config: Dict[str, Any], logger) -> AuthStrategy:
    """Construct the right AuthStrategy for the connector's auth_type."""
    auth_type = (config.get("auth_type") or "none").lower()
    base_url = config.get("url", "")
    # Threaded through to every strategy so disabling TLS verification on
    # the connector applies to the auth call too (e.g. login_password POST
    # to a self-signed login endpoint).
    verify_ssl = config.get("verify_ssl", True)

    if auth_type == "bearer":
        return APIKeyAuth(
            {
                "api_key": config.get("bearer_token"),
                "api_key_header": "Authorization",
                "api_key_prefix": "Bearer ",
                "location": "header",
                "base_url": base_url,
                "verify_ssl": verify_ssl,
            },
            logger,
        )
    if auth_type == "api_key":
        return APIKeyAuth(
            {
                "api_key": config.get("api_key"),
                "api_key_header": config.get("api_key_name", "X-API-Key"),
                "api_key_param": config.get("api_key_name", "X-API-Key"),
                "api_key_prefix": config.get("api_key_prefix", ""),
                "location": (config.get("api_key_location") or "header").lower(),
                "base_url": base_url,
                "verify_ssl": verify_ssl,
            },
            logger,
        )
    if auth_type == "basic":
        return get_auth_strategy(
            {
                "auth_type": "basic",
                "username": config.get("username"),
                "password": config.get("password"),
                "base_url": base_url,
                "verify_ssl": verify_ssl,
            },
            logger,
        )
    if auth_type == "oauth2":
        return get_auth_strategy(
            {
                "auth_type": "oauth2",
                "token_url": config.get("token_url"),
                "client_id": config.get("client_id"),
                "client_secret": config.get("client_secret"),
                "scope": config.get("scope"),
                "base_url": base_url,
                "verify_ssl": verify_ssl,
            },
            logger,
        )
    if auth_type == "login_password":
        return get_auth_strategy(
            {
                "auth_type": "login_password",
                "login_url": config.get("login_url"),
                "identity_field": config.get("identity_field") or "identity",
                "identity": config.get("identity"),
                "password_field": config.get("password_field") or "password",
                "password": config.get("password"),
                "token_path": config.get("token_path") or "$.token",
                "token_header": config.get("token_header") or "Authorization",
                "token_prefix": config.get("token_prefix") or "",
                "token_ttl_seconds": config.get("token_ttl_seconds") or 900,
                "base_url": base_url,
                "verify_ssl": verify_ssl,
            },
            logger,
        )
    # default: none
    return NoAuth({"base_url": base_url, "verify_ssl": verify_ssl}, logger)
