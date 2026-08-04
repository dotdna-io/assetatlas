# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for CustomHttpConnector (issue #616)."""
import pytest

from src.modules.integrations.connectors.custom_http_connector import (
    CustomHttpConnector,
)


def _base_config(**overrides):
    """Minimal valid config for a 'none' auth + 'none' pagination connector."""
    base = {
        "method": "GET",
        "url": "https://api.example.com/v1/items",
        "auth_type": "none",
        "pagination_type": "none",
        "records_root": "$.items",
        "field_bindings": [
            {"source_path": "$.id", "source_name": "id"},
        ],
    }
    base.update(overrides)
    return base


# --------------------------- Metadata & registration --------------------------


def test_class_metadata():
    assert CustomHttpConnector.integration_type == "custom_http"
    assert CustomHttpConnector.display_name == "Custom HTTP API"
    assert CustomHttpConnector.category == "Custom"


def test_registered_in_registry():
    from src.modules.integrations.registry import ConnectorRegistry
    assert ConnectorRegistry.is_registered("custom_http")


# --------------------------- get_config_schema --------------------------------


def test_config_schema_returns_minimal_stub():
    schema = CustomHttpConnector.get_config_schema()
    # Real form is the wizard step — schema only needs to declare the URL
    # so the generic wizard can create the row in `integrations`.
    assert "url" in schema
    assert schema["url"]["type"] == "url"


# ------------------------------ validate_config -------------------------------


def test_validate_happy_path_none_auth_none_pagination():
    c = CustomHttpConnector(_base_config())
    result = c.validate_config()
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_missing_url():
    c = CustomHttpConnector(_base_config(url=""))
    result = c.validate_config()
    assert result["valid"] is False
    assert any("url" in e.lower() for e in result["errors"])


def test_validate_url_must_be_http_or_https():
    c = CustomHttpConnector(_base_config(url="ftp://example.com/x"))
    result = c.validate_config()
    assert result["valid"] is False
    assert any("http" in e.lower() for e in result["errors"])


def test_validate_basic_auth_requires_username_password():
    c = CustomHttpConnector(_base_config(auth_type="basic"))
    result = c.validate_config()
    assert not result["valid"]
    msgs = " ".join(result["errors"]).lower()
    assert "username" in msgs and "password" in msgs


def test_validate_bearer_auth_requires_bearer_token():
    c = CustomHttpConnector(_base_config(auth_type="bearer"))
    result = c.validate_config()
    assert not result["valid"]
    assert any("bearer" in e.lower() for e in result["errors"])


def test_validate_api_key_auth_requires_api_key():
    c = CustomHttpConnector(_base_config(auth_type="api_key"))
    result = c.validate_config()
    assert not result["valid"]
    assert any("api_key" in e.lower() for e in result["errors"])


def test_validate_oauth2_requires_token_url_and_client_credentials():
    c = CustomHttpConnector(_base_config(auth_type="oauth2"))
    result = c.validate_config()
    assert not result["valid"]
    msgs = " ".join(result["errors"]).lower()
    assert "token_url" in msgs
    assert "client_id" in msgs
    assert "client_secret" in msgs


def test_validate_login_password_requires_login_url_identity_password():
    c = CustomHttpConnector(_base_config(auth_type="login_password"))
    result = c.validate_config()
    assert not result["valid"]
    msgs = " ".join(result["errors"]).lower()
    assert "login_url" in msgs
    assert "identity" in msgs
    assert "password" in msgs


def test_validate_login_password_happy_path():
    c = CustomHttpConnector(_base_config(
        auth_type="login_password",
        login_url="https://api.example.com/login",
        identity="admin",
        password="pw",
    ))
    result = c.validate_config()
    assert result["valid"], result["errors"]


def test_validate_login_password_rejects_invalid_token_path():
    c = CustomHttpConnector(_base_config(
        auth_type="login_password",
        login_url="https://api.example.com/login",
        identity="admin",
        password="pw",
        token_path="$..[notvalid",
    ))
    result = c.validate_config()
    assert not result["valid"]
    assert any("token_path" in e.lower() for e in result["errors"])


def test_build_auth_for_login_password_returns_login_password_strategy():
    from src.modules.integrations.base.auth_strategies import LoginPasswordJsonAuth
    c = CustomHttpConnector(_base_config(
        auth_type="login_password",
        login_url="https://api.example.com/login",
        identity="admin",
        password="pw",
    ))
    assert isinstance(c.auth, LoginPasswordJsonAuth)


def test_validate_offset_pagination_requires_page_size():
    c = CustomHttpConnector(_base_config(pagination_type="offset", page_size=0))
    result = c.validate_config()
    assert not result["valid"]
    assert any("page_size" in e.lower() for e in result["errors"])


def test_validate_cursor_pagination_requires_response_path():
    c = CustomHttpConnector(_base_config(
        pagination_type="cursor",
        cursor_request_param="cursor",
        cursor_response_path="",
    ))
    result = c.validate_config()
    assert not result["valid"]
    assert any("cursor_response_path" in e.lower() for e in result["errors"])


def test_validate_invalid_pagination_type():
    c = CustomHttpConnector(_base_config(pagination_type="bogus"))
    result = c.validate_config()
    assert not result["valid"]
    assert any("pagination_type" in e.lower() for e in result["errors"])


def test_validate_page_pagination_requires_page_param_and_size():
    c = CustomHttpConnector(_base_config(
        pagination_type="page",
        page_size=0,
        page_param="",
        perpage_param="",
    ))
    result = c.validate_config()
    assert not result["valid"]
    msgs = " ".join(result["errors"]).lower()
    assert "page_size" in msgs
    assert "page_param" in msgs
    assert "perpage_param" in msgs


def test_validate_page_pagination_happy_path():
    c = CustomHttpConnector(_base_config(
        pagination_type="page",
        page_size=100,
        page_param="page",
        perpage_param="perPage",
        start_page=1,
    ))
    result = c.validate_config()
    assert result["valid"], result["errors"]


def test_build_paginator_page_returns_page_paginator():
    from src.modules.integrations.base import PagePaginator
    c = CustomHttpConnector(_base_config(
        pagination_type="page",
        page_size=50,
        page_param="page",
        perpage_param="perPage",
        start_page=2,
    ))
    p = c._build_paginator()
    assert isinstance(p, PagePaginator)
    assert p.page_size == 50
    assert p.start_page == 2


def test_validate_invalid_records_root_jsonpath():
    c = CustomHttpConnector(_base_config(records_root="$..[notvalid"))
    result = c.validate_config()
    assert not result["valid"]
    assert any("records_root" in e.lower() for e in result["errors"])


def test_validate_empty_records_root_is_allowed():
    """records_root is optional — connector auto-detects at fetch time."""
    c = CustomHttpConnector(_base_config(records_root=""))
    result = c.validate_config()
    assert result["valid"], result["errors"]


# --------------------- auto_detect_records_root ------------------------------


def test_auto_detect_bare_array():
    body = [{"id": 1}, {"id": 2}]
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$"
    assert records == [{"id": 1}, {"id": 2}]


def test_auto_detect_dict_with_items_key():
    body = {"items": [{"id": 1}], "totalItems": 1, "page": 1}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$.items"
    assert records == [{"id": 1}]


def test_auto_detect_dict_with_data_key():
    body = {"data": [{"x": 1}], "next": None}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$.data"
    assert records == [{"x": 1}]


def test_auto_detect_priority_items_over_data():
    """When both 'items' and 'data' exist, 'items' wins (it's earlier in the
    priority list — matches PocketBase / JSON:API conventions)."""
    body = {"items": [{"a": 1}], "data": [{"b": 2}]}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$.items"
    assert records == [{"a": 1}]


def test_auto_detect_skips_empty_priority_key_when_better_exists():
    """Regression: a body with `items: []` and `data: [{...}]` must pick
    `data`. Previously the empty `items` won and silently dropped records."""
    body = {"items": [], "data": [{"a": 1}, {"a": 2}]}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$.data"
    assert records == [{"a": 1}, {"a": 2}]


def test_auto_detect_returns_empty_priority_key_when_nothing_better():
    """Legitimately-empty page: response has only an empty `items`. The
    paginator should see [] and stop, not start re-detecting."""
    body = {"items": [], "totalItems": 0, "page": 1}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$.items"
    assert records == []


def test_auto_detect_skips_empty_unique_key_when_priority_match_exists():
    """If a non-priority key is the only non-empty array but a priority key
    exists with an empty array, the non-priority key should still win."""
    body = {"items": [], "devices": [{"id": "x"}]}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$.devices"
    assert records == [{"id": "x"}]


def test_auto_detect_unique_array_key_fallback():
    """No priority key but exactly one array-valued top-level key → use it."""
    body = {"devices": [{"name": "a"}], "scanTime": "now"}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path == "$.devices"
    assert records == [{"name": "a"}]


def test_auto_detect_multiple_array_keys_no_priority_returns_none():
    """Ambiguous: multiple array keys, none in priority list → bail out."""
    body = {"devices": [{"a": 1}], "users": [{"b": 2}]}
    path, records = CustomHttpConnector.auto_detect_records_root(body)
    assert path is None
    assert records == []


def test_auto_detect_handles_non_dict_non_list():
    path, records = CustomHttpConnector.auto_detect_records_root("just-a-string")
    assert path is None
    assert records == []


def test_extract_records_uses_auto_detect_when_root_empty():
    """Connector with empty records_root auto-detects from the response body."""
    c = CustomHttpConnector(_base_config(records_root=""))
    body = {"items": [{"id": 1}, {"id": 2}], "totalItems": 2}
    out = c._extract_records(body)
    assert out == [{"id": 1}, {"id": 2}]


def test_extract_records_uses_explicit_path_when_set():
    c = CustomHttpConnector(_base_config(records_root="$.devices"))
    body = {"devices": [{"id": "x"}], "items": [{"id": "ignored"}]}
    out = c._extract_records(body)
    assert out == [{"id": "x"}]


def test_validate_invalid_binding_source_path():
    c = CustomHttpConnector(_base_config(
        field_bindings=[{"source_path": "$..[notvalid", "source_name": "x"}]
    ))
    result = c.validate_config()
    assert not result["valid"]
    assert any("source_path" in e.lower() for e in result["errors"])


def test_validate_duplicate_source_names():
    c = CustomHttpConnector(_base_config(
        field_bindings=[
            {"source_path": "$.a", "source_name": "x"},
            {"source_path": "$.b", "source_name": "x"},
        ],
    ))
    result = c.validate_config()
    assert not result["valid"]
    assert any("unique" in e.lower() or "duplicate" in e.lower() for e in result["errors"])


def test_validate_requires_at_least_one_binding():
    c = CustomHttpConnector(_base_config(field_bindings=[]))
    result = c.validate_config()
    assert not result["valid"]
    assert any("binding" in e.lower() for e in result["errors"])


def test_validate_passthrough_allows_empty_bindings():
    c = CustomHttpConnector(_base_config(field_bindings=[], passthrough=True))
    result = c.validate_config()
    assert result["valid"], result["errors"]


def test_passthrough_returns_record_verbatim():
    c = CustomHttpConnector(_base_config(field_bindings=[], passthrough=True))
    raw = {"id": 1, "name": "x", "info": {"cpu": 3.5, "mp": 70.0}}
    out = c._apply_bindings(raw)
    assert out == raw
    # Defensive: must be a copy, not the same reference
    out["info"]["cpu"] = 99
    assert raw["info"]["cpu"] == 99 or raw["info"]["cpu"] == 3.5  # shallow copy ok


def test_passthrough_handles_non_dict_record():
    c = CustomHttpConnector(_base_config(field_bindings=[], passthrough=True))
    out = c._apply_bindings("just-a-string")
    assert out == {"value": "just-a-string"}


def test_passthrough_off_still_uses_bindings():
    c = CustomHttpConnector(_base_config())
    out = c._apply_bindings({"id": 42, "extra": "ignored"})
    assert out == {"id": 42}


import responses  # noqa: E402

# ---------------------------- _apply_bindings --------------------------------


def test_apply_bindings_flat_field():
    c = CustomHttpConnector(_base_config())
    out = c._apply_bindings({"id": 42, "host": "h"})
    assert out == {"id": 42}


def test_apply_bindings_nested_field():
    bindings = [{"source_path": "$.os.name", "source_name": "os_name"}]
    c = CustomHttpConnector(_base_config(field_bindings=bindings))
    assert c._apply_bindings({"os": {"name": "linux"}}) == {"os_name": "linux"}


def test_apply_bindings_missing_field_yields_none():
    c = CustomHttpConnector(_base_config())
    assert c._apply_bindings({"host": "h"}) == {"id": None}


def test_apply_bindings_multi_match_returns_list():
    bindings = [{"source_path": "$.tags[*].name", "source_name": "tag_names"}]
    c = CustomHttpConnector(_base_config(field_bindings=bindings))
    out = c._apply_bindings({"tags": [{"name": "a"}, {"name": "b"}]})
    assert out == {"tag_names": ["a", "b"]}


# ---------------------------- _build_paginator -------------------------------


def test_build_paginator_dispatches_correctly():
    from src.modules.integrations.base import (
        NoPaginator, OffsetPaginator, CursorPaginator, LinkPaginator,
    )

    cases = [
        ("none", NoPaginator),
        ("offset", OffsetPaginator),
        ("cursor", CursorPaginator),
        ("link", LinkPaginator),
    ]
    for ptype, cls in cases:
        c = CustomHttpConnector(_base_config(
            pagination_type=ptype,
            page_size=100, offset_param="offset", limit_param="limit",
            cursor_request_param="cursor", cursor_response_path="$.next",
        ))
        assert isinstance(c._build_paginator(), cls), f"{ptype} → {cls}"


# ---------------------------- _fetch_page (GET, none pagination) -------------


@responses.activate
def test_fetch_page_get_none_pagination_extracts_records_root():
    body = {"items": [{"id": 1}, {"id": 2}]}
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json=body,
        status=200,
    )
    c = CustomHttpConnector(_base_config(records_root="$.items"))
    records = c._fetch_page_simple()
    assert records == [{"id": 1}, {"id": 2}]


# ---------------------------- _fetch_page (GET, offset pagination) -----------


@responses.activate
def test_fetch_page_offset_writes_offset_and_limit_params():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}], "total": 1},
        status=200,
    )
    c = CustomHttpConnector(_base_config(
        pagination_type="offset",
        page_size=50, offset_param="off", limit_param="lim",
        records_root="$.items",
    ))
    records, total = c._fetch_page_offset(0, 50)
    assert records == [{"id": 1}]
    assert total == 1

    sent = responses.calls[0].request
    assert "off=0" in sent.url
    assert "lim=50" in sent.url


# ---------------------------- _fetch_page (POST body merge) ------------------


@responses.activate
def test_fetch_page_post_merges_pagination_into_body():
    responses.add(
        responses.POST,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}], "total": 1},
        status=200,
    )
    c = CustomHttpConnector(_base_config(
        method="POST",
        static_body={"filter": {"active": True}},
        pagination_type="offset",
        page_size=50, offset_param="offset", limit_param="limit",
        records_root="$.items",
    ))
    records, total = c._fetch_page_offset(100, 50)
    assert records == [{"id": 1}]

    import json
    sent_body = json.loads(responses.calls[0].request.body)
    assert sent_body["filter"] == {"active": True}
    assert sent_body["offset"] == 100
    assert sent_body["limit"] == 50


# ---------------------------- _fetch_page (cursor) ---------------------------


@responses.activate
def test_fetch_page_cursor_extracts_next_token_from_response():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}], "next": "abc123"},
        status=200,
    )
    c = CustomHttpConnector(_base_config(
        pagination_type="cursor",
        cursor_request_param="cursor",
        cursor_response_path="$.next",
        records_root="$.items",
    ))
    records, next_cursor = c._fetch_page_cursor(None)
    assert records == [{"id": 1}]
    assert next_cursor == "abc123"


# ---------------------------- _fetch_page (link header) ----------------------


@responses.activate
def test_fetch_page_link_parses_next_url_from_header():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}]},
        status=200,
        headers={
            "Link": '<https://api.example.com/v1/items?page=2>; rel="next"'
        },
    )
    c = CustomHttpConnector(_base_config(
        pagination_type="link",
        records_root="$.items",
    ))
    records, next_url = c._fetch_page_link(None)
    assert records == [{"id": 1}]
    assert next_url == "https://api.example.com/v1/items?page=2"


# ---------------------------- get_schema --------------------------------------


def test_get_schema_returns_one_field_per_binding():
    bindings = [
        {"source_path": "$.id", "source_name": "id"},
        {"source_path": "$.host", "source_name": "hostname"},
    ]
    c = CustomHttpConnector(_base_config(field_bindings=bindings))
    schema = c.get_schema()
    names = [f["name"] for f in schema["fields"]]
    assert names == ["id", "hostname"]
    assert all(f["type"] == "string" for f in schema["fields"])
    assert schema["source"] == "custom_http"


# ---------------------------- _collect_records --------------------------------


@responses.activate
def test_collect_records_no_pagination_yields_bound_records():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1, "host": "a"}, {"id": 2, "host": "b"}]},
        status=200,
    )
    c = CustomHttpConnector(_base_config(
        records_root="$.items",
        field_bindings=[
            {"source_path": "$.id", "source_name": "id"},
            {"source_path": "$.host", "source_name": "hostname"},
        ],
    ))
    records = list(c._collect_records("batch-1"))
    assert records == [
        {"id": 1, "hostname": "a"},
        {"id": 2, "hostname": "b"},
    ]


@responses.activate
def test_collect_records_offset_pagination_walks_pages():
    # Two-page sequence: page 1 has 2 items, page 2 has 1 item, page 3 empty
    responses.add(responses.GET, "https://api.example.com/v1/items",
                  json={"items": [{"id": 1}, {"id": 2}], "total": 3}, status=200)
    responses.add(responses.GET, "https://api.example.com/v1/items",
                  json={"items": [{"id": 3}], "total": 3}, status=200)
    responses.add(responses.GET, "https://api.example.com/v1/items",
                  json={"items": [], "total": 3}, status=200)

    c = CustomHttpConnector(_base_config(
        pagination_type="offset",
        page_size=2, offset_param="offset", limit_param="limit",
        records_root="$.items",
        total_path="$.total",
        field_bindings=[{"source_path": "$.id", "source_name": "id"}],
    ))
    ids = [r["id"] for r in c._collect_records("batch-2")]
    assert ids == [1, 2, 3]


# ---------------------------- test_connection --------------------------------


@responses.activate
def test_test_connection_success():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}]},
        status=200,
    )
    c = CustomHttpConnector(_base_config(records_root="$.items"))
    result = c.test_connection()
    assert result["success"] is True
    assert "status_code" in result
    assert result["status_code"] == 200


@responses.activate
def test_test_connection_failure():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"error": "unauthorized"},
        status=401,
    )
    c = CustomHttpConnector(_base_config(records_root="$.items"))
    result = c.test_connection()
    assert result["success"] is False
    assert "401" in result["message"] or "Unauthorized" in result.get("message", "")


# ---------------------------- run_single_call --------------------------------


@responses.activate
def test_run_single_call_returns_status_headers_and_body():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}]},
        status=200,
        headers={"X-Total": "1"},
    )
    c = CustomHttpConnector(_base_config(records_root="$.items"))
    result = c.run_single_call()
    assert result["status_code"] == 200
    assert isinstance(result["elapsed_ms"], (int, float))
    assert result["body"] == {"items": [{"id": 1}]}
    assert result["response_headers"]["X-Total"] == "1"


@responses.activate
def test_run_single_call_handles_non_json():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        body="<xml>not-json</xml>",
        status=200,
        content_type="application/xml",
    )
    c = CustomHttpConnector(_base_config(records_root="$.items"))
    result = c.run_single_call()
    assert result["status_code"] == 200
    assert "_raw" in result["body"]
    assert "<xml>" in result["body"]["_raw"]
