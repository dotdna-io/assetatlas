# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSONPath helper for the custom HTTP connector.

Wraps `jsonpath_ng.ext.parse` (the *extended* parser that supports filter
expressions and recursive descent) with an LRU-cached compiler so the same
path string is only compiled once across millions of records.
"""
from __future__ import annotations

import functools
from typing import Any, List, Optional

from jsonpath_ng.ext import parse as _parse  # type: ignore[import-untyped]
from jsonpath_ng.jsonpath import JSONPath  # type: ignore[import-untyped]


@functools.lru_cache(maxsize=512)
def _compile(path: str) -> JSONPath:
    """Compile a JSONPath string. Cached so repeated paths cost ~nothing."""
    return _parse(path)


def extract_first(data: Any, path: str, default: Any = None) -> Any:
    """Return the first matching value, or `default` if no match."""
    matches = _compile(path).find(data)
    return matches[0].value if matches else default


def extract_many(data: Any, path: str) -> List[Any]:
    """Return all matching values as a list (empty if no match)."""
    return [m.value for m in _compile(path).find(data)]


def validate(path: str) -> Optional[str]:
    """Return None if the path is syntactically valid, else an error string.

    Used by `CustomHttpConnector.validate_config()` to surface parse errors
    to the wizard before the user saves. Goes through `_compile` so a
    successful validate warms the LRU cache for the first sync.
    """
    try:
        _compile(path)
        return None
    except Exception as e:
        return str(e)
