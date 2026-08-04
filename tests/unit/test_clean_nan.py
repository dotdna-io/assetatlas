# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for _clean_nan() and _is_nan_or_nat() from postgres_adapter.py.

These are pure utility functions with no database dependencies.
"""

import math

import pytest

from src.database.postgres_adapter import _clean_nan, _is_nan_or_nat


# ============================================================================
# _is_nan_or_nat
# ============================================================================


class TestIsNanOrNat:
    """Tests for _is_nan_or_nat()."""

    def test_float_nan(self):
        assert _is_nan_or_nat(float("nan")) is True

    def test_math_nan(self):
        assert _is_nan_or_nat(math.nan) is True

    def test_numpy_nan(self):
        np = pytest.importorskip("numpy")
        assert _is_nan_or_nat(np.nan) is True

    def test_pandas_nat(self):
        pd = pytest.importorskip("pandas")
        assert _is_nan_or_nat(pd.NaT) is True

    def test_pandas_na(self):
        pd = pytest.importorskip("pandas")
        assert _is_nan_or_nat(pd.NA) is True

    def test_normal_float(self):
        assert _is_nan_or_nat(3.14) is False

    def test_zero(self):
        assert _is_nan_or_nat(0) is False

    def test_none(self):
        # pandas.isna(None) returns True, so _is_nan_or_nat(None) is True
        assert _is_nan_or_nat(None) is True

    def test_empty_string(self):
        assert _is_nan_or_nat("") is False

    def test_normal_string(self):
        assert _is_nan_or_nat("hello") is False

    def test_integer(self):
        assert _is_nan_or_nat(42) is False

    def test_boolean_false(self):
        assert _is_nan_or_nat(False) is False

    def test_boolean_true(self):
        assert _is_nan_or_nat(True) is False

    def test_empty_list(self):
        """Lists raise ValueError in pandas.isna, should return False."""
        assert _is_nan_or_nat([]) is False

    def test_empty_dict(self):
        """Dicts raise ValueError in pandas.isna, should return False."""
        assert _is_nan_or_nat({}) is False


# ============================================================================
# _clean_nan
# ============================================================================


class TestCleanNan:
    """Tests for _clean_nan()."""

    def test_flat_dict_with_nan(self):
        result = _clean_nan({"a": 1, "b": float("nan"), "c": "hello"})
        assert result == {"a": 1, "b": None, "c": "hello"}

    def test_nested_dict(self):
        result = _clean_nan({"outer": {"inner": float("nan"), "ok": 5}})
        assert result == {"outer": {"inner": None, "ok": 5}}

    def test_list_with_nan(self):
        result = _clean_nan([1, float("nan"), "x"])
        assert result == [1, None, "x"]

    def test_no_nan_passthrough(self):
        data = {"a": 1, "b": "text", "c": True}
        result = _clean_nan(data)
        assert result == data

    def test_empty_dict(self):
        assert _clean_nan({}) == {}

    def test_empty_list(self):
        assert _clean_nan([]) == []

    def test_deeply_nested(self):
        data = {"l1": {"l2": {"l3": [float("nan"), {"l4": float("nan")}]}}}
        expected = {"l1": {"l2": {"l3": [None, {"l4": None}]}}}
        assert _clean_nan(data) == expected

    def test_mixed_types_in_list(self):
        data = [1, "two", None, float("nan"), True, 3.14, {"k": float("nan")}]
        expected = [1, "two", None, None, True, 3.14, {"k": None}]
        assert _clean_nan(data) == expected

    def test_scalar_nan(self):
        assert _clean_nan(float("nan")) is None

    def test_scalar_non_nan(self):
        assert _clean_nan(42) == 42
        assert _clean_nan("hello") == "hello"
        assert _clean_nan(None) is None

    def test_math_nan(self):
        result = _clean_nan({"x": math.nan})
        assert result == {"x": None}

    def test_dict_with_list_value(self):
        data = {"items": [1.0, float("nan"), 3.0]}
        expected = {"items": [1.0, None, 3.0]}
        assert _clean_nan(data) == expected

    def test_preserves_non_nan_floats(self):
        data = {"pi": 3.14159, "zero": 0.0, "neg": -1.5}
        assert _clean_nan(data) == data
