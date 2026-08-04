# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the semantic_suggest MCP tool."""

from unittest.mock import MagicMock

from src.mcp.tools.suggestions import semantic_suggest_impl


def test_semantic_suggest_returns_field_matches():
    svc = MagicMock()
    svc.get_suggestions.return_value = {
        "suggestions": [
            {"type": "field", "field_name": "hostname", "display_name": "Hostname",
             "score": 0.92, "field_type": "string", "templates": []},
            {"type": "field", "field_name": "fqdn", "display_name": "FQDN",
             "score": 0.81, "field_type": "string", "templates": []},
        ],
        "query": "machine name",
        "query_time_ms": 12.3,
        "total_found": 2,
    }
    out = semantic_suggest_impl(svc, query="machine name", limit=5, min_score=0.5)
    assert "hostname" in out
    assert "0.92" in out
    assert "fqdn" in out


def test_semantic_suggest_empty_matches():
    svc = MagicMock()
    svc.get_suggestions.return_value = {"suggestions": [], "query_time_ms": 1.0,
                                        "total_found": 0}
    out = semantic_suggest_impl(svc, query="xyz", limit=5, min_score=0.5)
    assert "no suggestions" in out.lower()


def test_semantic_suggest_service_unavailable():
    svc = MagicMock()
    svc.get_suggestions.return_value = {
        "suggestions": [],
        "service_unavailable": True,
        "message": "chromadb not installed",
    }
    out = semantic_suggest_impl(svc, query="ok", limit=5, min_score=0.5)
    assert "chromadb not installed" in out.lower()


def test_semantic_suggest_short_query_returns_message():
    svc = MagicMock()
    svc.get_suggestions.return_value = {
        "suggestions": [],
        "message": "Query too short (min 2 characters)",
    }
    out = semantic_suggest_impl(svc, query="a", limit=5, min_score=0.5)
    assert "too short" in out.lower()


def test_semantic_suggest_none_service_returns_helpful_message():
    out = semantic_suggest_impl(None, query="anything", limit=5, min_score=0.5)
    assert "not available" in out.lower()


def test_semantic_suggest_clamps_limit_and_score():
    svc = MagicMock()
    svc.get_suggestions.return_value = {"suggestions": [], "total_found": 0}
    semantic_suggest_impl(svc, query="x", limit=9999, min_score=2.5)
    kwargs = svc.get_suggestions.call_args.kwargs
    assert kwargs["limit"] == 20
    assert kwargs["min_score"] == 1.0


def test_semantic_suggest_handles_runtime_exception():
    svc = MagicMock()
    svc.get_suggestions.side_effect = RuntimeError("chromadb timeout")
    out = semantic_suggest_impl(svc, query="hostname", limit=5, min_score=0.5)
    assert "error" in out.lower()
