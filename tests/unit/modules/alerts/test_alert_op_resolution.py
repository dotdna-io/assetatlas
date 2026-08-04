# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alert read path normalizes operator spellings to canonical codes."""

from src.modules.alerts.repository import _to_compiler_filter


def test_label_op_resolves_to_code():
    out = _to_compiler_filter({"field": "x", "op": "any of", "value": "a,b"})
    assert out["operator"] == "any"


def test_code_op_is_fixed_point():
    out = _to_compiler_filter({"field": "x", "op": "eq", "value": "a"})
    assert out["operator"] == "eq"


def test_unknown_op_passes_through_for_loud_compiler_failure():
    # Resolution failure is left to the compiler's loud-fail; _to_compiler_filter
    # must not silently drop — it passes the spelling through unchanged.
    out = _to_compiler_filter({"field": "x", "op": "bogus", "value": "a"})
    assert out["operator"] == "bogus"
