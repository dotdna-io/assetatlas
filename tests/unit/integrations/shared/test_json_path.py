# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the JSONPath helper used by the custom HTTP connector."""
from src.modules.integrations.shared.json_path import (
    extract_first,
    extract_many,
    validate,
)


SAMPLE = {
    "data": {
        "items": [
            {"id": 1, "host": "a", "tags": [{"name": "x"}, {"name": "y"}]},
            {"id": 2, "host": "b", "tags": []},
        ],
        "total": 2,
    },
    "meta": {"page": 1},
}


def test_extract_first_returns_value_for_path():
    assert extract_first(SAMPLE, "$.data.total") == 2


def test_extract_first_returns_default_when_missing():
    assert extract_first(SAMPLE, "$.data.missing", default="dflt") == "dflt"


def test_extract_first_returns_default_when_none_supplied():
    assert extract_first(SAMPLE, "$.nope") is None


def test_extract_many_returns_list_of_matches():
    assert extract_many(SAMPLE, "$.data.items[*].id") == [1, 2]


def test_extract_many_returns_empty_when_no_match():
    assert extract_many(SAMPLE, "$.data.items[*].missing") == []


def test_extract_many_handles_recursive_descent():
    # $..name walks any 'name' anywhere
    assert extract_many(SAMPLE, "$..name") == ["x", "y"]


def test_validate_returns_none_for_valid_path():
    assert validate("$.data.items[*]") is None


def test_validate_returns_error_string_for_invalid_path():
    err = validate("$..[notvalid")
    assert err is not None
    assert isinstance(err, str)


def test_compile_is_lru_cached():
    """Repeat parses of the same path should hit the cache."""
    from src.modules.integrations.shared.json_path import _compile
    _compile.cache_clear()
    try:
        _compile("$.a.b")
        _compile("$.a.b")
        info = _compile.cache_info()
        assert info.hits == 1
        assert info.misses == 1
    finally:
        _compile.cache_clear()
