# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the unified-fields MCP read tools (#939).

The impls wrap UnifiedFieldsService.list() / .get(), so the service is a
MagicMock and we assert the rendered text + that a NotFoundError surfaces as a
clean 'not found' message (not a traceback)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.api.middleware.error_handler import NotFoundError
from src.mcp.tools.unified_fields import (
    get_unified_field_impl,
    list_unified_fields_impl,
)

_FIELD_ID = "550e8400-e29b-41d4-a716-446655440000"


def _field(**overrides):
    base = {
        "id": _FIELD_ID,
        "name": "Primary Hostname",
        "function": "coalesce",
        "sources": ["static:Hostname", "ext:Jamf:device_name"],
        "data_type": "string",
        "encoded_key": "virtual:primary_hostname",
        "created_at": "2026-07-01T00:00:00",
        "updated_at": "2026-07-02T00:00:00",
    }
    base.update(overrides)
    return base


# --- list_unified_fields_impl ------------------------------------------------

def test_list_none_service_returns_unavailable():
    assert list_unified_fields_impl(None) == (
        "Unified Fields service not available.")


def test_list_empty_reports_zero():
    svc = MagicMock(list=MagicMock(return_value=[]))
    out = list_unified_fields_impl(svc)
    assert out.startswith("COUNT: 0 unified fields defined")


def test_list_renders_fields_with_key_function_and_sources():
    svc = MagicMock(list=MagicMock(return_value=[
        _field(),
        _field(id="00000000-0000-0000-0000-000000000002",
               name="Best OS", function="first_non_empty",
               sources=["static:OS Normalized", "ext:Intune:os"],
               encoded_key="virtual:best_os"),
    ]))
    out = list_unified_fields_impl(svc)
    assert "COUNT: 2 unified field(s)." in out
    assert "Primary Hostname" in out and "Best OS" in out
    # the encoded catalog keys (what makes them queryable) are surfaced
    assert "virtual:primary_hostname" in out and "virtual:best_os" in out
    # function + source fields are surfaced for both
    assert "coalesce" in out and "first_non_empty" in out
    assert "ext:Jamf:device_name" in out


def test_list_service_exception_is_contained():
    svc = MagicMock()
    svc.list.side_effect = RuntimeError("boom")
    out = list_unified_fields_impl(svc)
    assert "Unified Fields unavailable:" in out and "boom" in out


# --- get_unified_field_impl --------------------------------------------------

def test_get_none_service_returns_unavailable():
    assert get_unified_field_impl(None, field_id=_FIELD_ID) == (
        "Unified Fields service not available.")


def test_get_invalid_uuid_is_rejected_without_hitting_service():
    svc = MagicMock()
    out = get_unified_field_impl(svc, field_id="not-a-uuid")
    assert "Invalid unified field ID" in out
    svc.get.assert_not_called()


def test_get_renders_single_field_detail():
    svc = MagicMock(get=MagicMock(return_value=_field()))
    out = get_unified_field_impl(svc, field_id=_FIELD_ID)
    assert "Primary Hostname" in out
    assert f"id={_FIELD_ID}" in out
    assert "coalesce" in out
    assert "virtual:primary_hostname" in out
    assert "static:Hostname" in out
    svc.get.assert_called_once_with(_FIELD_ID)


def test_get_missing_field_surfaces_clean_not_found():
    svc = MagicMock()
    svc.get.side_effect = NotFoundError("Unified field ... not found")
    out = get_unified_field_impl(svc, field_id=_FIELD_ID)
    assert out == f"Unified field {_FIELD_ID} not found."


def test_get_other_exception_is_contained():
    svc = MagicMock()
    svc.get.side_effect = RuntimeError("boom")
    out = get_unified_field_impl(svc, field_id=_FIELD_ID)
    assert "Unified field unavailable:" in out and "boom" in out
