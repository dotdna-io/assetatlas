# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for RFC 5988 Link-header parser."""
from src.modules.integrations.shared.rfc5988_link import parse_next_link


def test_returns_url_for_rel_next_double_quoted():
    h = '<https://api.example.com/v1/items?page=2>; rel="next"'
    assert parse_next_link(h) == "https://api.example.com/v1/items?page=2"


def test_returns_url_for_rel_next_single_quoted():
    h = "<https://api.example.com/x?p=2>; rel='next'"
    assert parse_next_link(h) == "https://api.example.com/x?p=2"


def test_returns_url_for_rel_next_unquoted():
    h = "<https://api.example.com/x?p=2>; rel=next"
    assert parse_next_link(h) == "https://api.example.com/x?p=2"


def test_picks_next_among_multiple_relations():
    h = (
        '<https://api/x?p=1>; rel="prev", '
        '<https://api/x?p=3>; rel="next", '
        '<https://api/x?p=10>; rel="last"'
    )
    assert parse_next_link(h) == "https://api/x?p=3"


def test_returns_none_when_only_prev_present():
    h = '<https://api/x?p=1>; rel="prev"'
    assert parse_next_link(h) is None


def test_returns_none_for_empty_or_none():
    assert parse_next_link("") is None
    assert parse_next_link(None) is None


def test_returns_none_for_malformed():
    assert parse_next_link("totally not a link header") is None


def test_strips_whitespace_around_url():
    h = '<  https://api/x?p=2  >; rel="next"'
    assert parse_next_link(h) == "https://api/x?p=2"
