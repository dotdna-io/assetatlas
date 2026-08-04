# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the audit trail MCP/chat tool.

Hardened to mirror the UI audit route (``src/api/blueprints/audit_trail``):
a REAL total COUNT (not a LIMIT+1 "more exist" guess), partial ILIKE match on
username + path, status_code OR status_class (e.g. "5xx"), and start/end dates
PARSED with a clean error string on bad input (an unparseable date used to be
handed straight to SQL → 500). request_body/response_summary are surfaced as
stored (already redacted server-side by the audit middleware).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from src.mcp.tools.audit import get_audit_trail_impl


def _count(n):
    """A COUNT(*) result row as the RealDict adapter returns it."""
    return [{"total": n}]


def _audit_row(**overrides):
    base = {
        "id": 1,
        "timestamp": "2026-04-20T10:00:00Z",
        "username": "alice",
        "method": "GET",
        "path": "/api/assets",
        "status_code": 200,
        "ip_address": "10.0.0.1",
        "duration_ms": 42,
        "request_id": "req-abc",
        "request_body": None,
        "response_summary": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Real COUNT + pagination                                                     #
# --------------------------------------------------------------------------- #

def test_empty_returns_no_match():
    db = MagicMock()
    db.execute_query.return_value = _count(0)
    out = get_audit_trail_impl(db)
    assert "No audit trail entries match" in out


def test_renders_rows_with_real_total():
    db = MagicMock()
    rows = [
        _audit_row(username="alice", path="/api/assets", status_code=200),
        _audit_row(id=2, username="bob", path="/api/alerts", status_code=404),
        _audit_row(id=3, username="carol", path="/api/reports", status_code=500),
    ]
    db.execute_query.side_effect = [_count(3), rows]

    out = get_audit_trail_impl(db)

    assert "alice" in out and "bob" in out and "carol" in out
    assert "/api/assets" in out and "/api/alerts" in out
    assert "200" in out and "404" in out and "500" in out
    # Real total in the header; single page, so no overflow hint.
    assert "3 total" in out
    assert "page 1 of 1" in out
    assert "more" not in out.lower()


def test_total_comes_from_count_not_row_length():
    """The header total must be the real COUNT, not len(rows). With 100 total
    but only 2 rows on the page, the old LIMIT+1 trick could only ever say
    "2+" — this proves a true count."""
    db = MagicMock()
    rows = [_audit_row(id=1), _audit_row(id=2)]
    db.execute_query.side_effect = [_count(100), rows]

    out = get_audit_trail_impl(db, per_page=2)

    assert "100 total" in out
    assert "page 1 of 50" in out


def test_overflow_hint_names_true_remaining_and_narrowing():
    db = MagicMock()
    rows = [_audit_row(id=1), _audit_row(id=2)]
    db.execute_query.side_effect = [_count(10), rows]

    out = get_audit_trail_impl(db, per_page=2, page=1)

    # 10 total, 2 shown → 8 more, across 5 pages.
    assert "8 more" in out
    assert "page 1 of 5" in out
    assert "narrow" in out.lower()


def test_count_query_mirrors_join():
    db = MagicMock()
    db.execute_query.return_value = _count(0)

    get_audit_trail_impl(db)

    count_sql = " ".join(db.execute_query.call_args_list[0].args[0].split()).lower()
    assert "count(*)" in count_sql
    assert "left join users u on u.id = a.user_id" in count_sql


def test_data_query_selects_bodies_and_joins_users():
    db = MagicMock()
    db.execute_query.side_effect = [_count(1), [_audit_row()]]

    get_audit_trail_impl(db)

    data_sql = " ".join(db.execute_query.call_args_list[1].args[0].split()).lower()
    assert "left join users u on u.id = a.user_id" in data_sql
    assert "u.username" in data_sql
    assert "a.request_body" in data_sql
    assert "a.response_summary" in data_sql


# --------------------------------------------------------------------------- #
# Date parsing — bad input is a STRING, not a 500                             #
# --------------------------------------------------------------------------- #

def test_bad_start_date_returns_error_string_without_querying():
    db = MagicMock()
    out = get_audit_trail_impl(db, start_date="not-a-date")
    assert "start_date" in out
    assert "Invalid" in out or "ISO" in out
    # Must fail BEFORE touching the DB (the old code let psycopg2 raise → 500).
    db.execute_query.assert_not_called()


def test_bad_end_date_returns_error_string_without_querying():
    db = MagicMock()
    out = get_audit_trail_impl(db, end_date="2026-13-99")
    assert "end_date" in out
    db.execute_query.assert_not_called()


def test_valid_date_is_parsed_to_datetime():
    db = MagicMock()
    db.execute_query.return_value = _count(0)

    get_audit_trail_impl(db, start_date="2026-04-01")

    call = db.execute_query.call_args_list[0]
    sql = call.args[0]
    params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
    assert "a.timestamp >= %(start_date)s" in sql
    assert isinstance(params["start_date"], datetime)
    assert params["start_date"] == datetime(2026, 4, 1)


# --------------------------------------------------------------------------- #
# status_code OR status_class                                                 #
# --------------------------------------------------------------------------- #

def test_status_class_5xx_filters_by_range():
    db = MagicMock()
    db.execute_query.return_value = _count(0)

    get_audit_trail_impl(db, status_class="5xx")

    call = db.execute_query.call_args_list[0]
    sql = call.args[0]
    params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
    assert "a.status_code >= %(status_min)s" in sql
    assert "a.status_code < %(status_max)s" in sql
    assert params["status_min"] == 500
    assert params["status_max"] == 600


def test_unknown_status_class_is_loud_error():
    db = MagicMock()
    out = get_audit_trail_impl(db, status_class="6xx")
    assert "status_class" in out
    assert "6xx" in out
    db.execute_query.assert_not_called()


def test_status_code_and_status_class_are_mutually_exclusive():
    db = MagicMock()
    out = get_audit_trail_impl(db, status_code=503, status_class="5xx")
    assert "status_code" in out and "status_class" in out
    db.execute_query.assert_not_called()


# --------------------------------------------------------------------------- #
# Partial ILIKE filters                                                       #
# --------------------------------------------------------------------------- #

def test_username_is_partial_ilike():
    db = MagicMock()
    db.execute_query.return_value = _count(0)

    get_audit_trail_impl(db, username="ali")

    call = db.execute_query.call_args_list[0]
    sql = call.args[0]
    params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
    assert "u.username ILIKE %(username)s" in sql
    assert params["username"] == "%ali%"


def test_path_is_partial_ilike_with_wildcards_escaped():
    db = MagicMock()
    db.execute_query.return_value = _count(0)

    get_audit_trail_impl(db, path="50%_x")

    call = db.execute_query.call_args_list[0]
    sql = call.args[0]
    params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
    assert "a.path ILIKE %(path)s ESCAPE" in sql
    # User-supplied % and _ are escaped so they match literally, not as
    # SQL wildcards.
    assert params["path"] == r"%50\%\_x%"


def test_method_is_uppercased():
    db = MagicMock()
    db.execute_query.return_value = _count(0)

    get_audit_trail_impl(db, method="post")

    call = db.execute_query.call_args_list[0]
    params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
    assert params["method"] == "POST"


# --------------------------------------------------------------------------- #
# Bodies surfaced, respecting server-side redaction                          #
# --------------------------------------------------------------------------- #

def test_request_body_and_response_summary_are_surfaced_as_stored():
    db = MagicMock()
    row = _audit_row(
        # The audit middleware already redacted the password field at write
        # time; the tool must surface it as stored, not re-expose it.
        request_body={"name": "x", "password": "***REDACTED***"},
        response_summary='{"ok": true, "count": 5}',
    )
    db.execute_query.side_effect = [_count(1), [row]]

    out = get_audit_trail_impl(db)

    assert "***REDACTED***" in out
    assert '"count": 5' in out or "count" in out


def test_path_truncation():
    db = MagicMock()
    long_path = "/api/" + ("x" * 120)
    db.execute_query.side_effect = [_count(1), [_audit_row(path=long_path)]]

    out = get_audit_trail_impl(db)
    assert "..." in out
    assert long_path not in out


def test_response_summary_with_token_is_redacted():
    """Security: response_summary is NOT sanitized at write time (only
    request_body is), so a login response carrying an access token must be
    redacted on display rather than leaked into the chat transcript."""
    db = MagicMock()
    row = _audit_row(
        path="/api/auth/login", method="POST", status_code=200,
        response_summary=(
            '{"access_token":"eyJhbGciOiJIUzI1NiI4xQ.payloadpart",'
            '"refresh_token":"rtok"}'
        ),
    )
    db.execute_query.side_effect = [_count(1), [row]]

    out = get_audit_trail_impl(db)

    assert "eyJhbGciOiJIUzI1NiI4xQ" not in out
    assert "access_token" not in out
    assert "redacted" in out.lower()


def test_response_summary_without_secret_is_shown():
    db = MagicMock()
    row = _audit_row(response_summary='{"total": 5, "items": []}')
    db.execute_query.side_effect = [_count(1), [row]]

    out = get_audit_trail_impl(db)

    # A benign body (no password/token/secret/... field) is surfaced.
    assert "total" in out


def test_non_string_method_and_path_do_not_crash():
    """The chat ToolRegistry intentionally skips JSON-Schema type validation,
    so a non-string method/path can reach the impl; it must coerce, not raise
    AttributeError on .upper()/.replace()."""
    db = MagicMock()
    db.execute_query.return_value = _count(0)

    out = get_audit_trail_impl(db, method=200, path=123)

    assert "No audit trail entries match" in out  # ran cleanly, no exception
    call = db.execute_query.call_args_list[0]
    params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
    assert params["method"] == "200"
    assert params["path"] == "%123%"
