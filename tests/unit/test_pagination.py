# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for PaginationParams and PaginationMeta from pagination.py.

Pure Pydantic model tests -- no Flask request context needed.
"""

import pytest
from pydantic import ValidationError

from src.api.utils.pagination import PaginationParams, PaginationMeta


# ============================================================================
# PaginationParams
# ============================================================================


class TestPaginationParams:
    """Tests for PaginationParams model."""

    def test_defaults(self):
        params = PaginationParams()
        assert params.page == 1
        assert params.limit == 50
        assert params.offset == 0

    def test_custom_values(self):
        params = PaginationParams(page=3, limit=20)
        assert params.page == 3
        assert params.limit == 20
        assert params.offset == 40  # (3-1) * 20

    def test_offset_auto_calculated(self):
        params = PaginationParams(page=5, limit=10)
        assert params.offset == 40  # (5-1) * 10

    def test_explicit_offset_preserved(self):
        params = PaginationParams(page=2, limit=10, offset=100)
        assert params.offset == 100  # explicit overrides calculated

    def test_page_less_than_1_rejected(self):
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

    def test_negative_page_rejected(self):
        with pytest.raises(ValidationError):
            PaginationParams(page=-1)

    def test_limit_less_than_1_rejected(self):
        with pytest.raises(ValidationError):
            PaginationParams(limit=0)

    def test_limit_exceeds_max_rejected(self):
        with pytest.raises(ValidationError):
            PaginationParams(limit=10001)

    def test_page_1_offset_is_zero(self):
        params = PaginationParams(page=1, limit=25)
        assert params.offset == 0


# ============================================================================
# PaginationMeta
# ============================================================================


class TestPaginationMeta:
    """Tests for PaginationMeta.from_params()."""

    def test_basic_case(self):
        meta = PaginationMeta.from_params(page=1, limit=10, total=100)
        assert meta.page == 1
        assert meta.limit == 10
        assert meta.total == 100
        assert meta.pages == 10
        assert meta.has_next is True
        assert meta.has_prev is False

    def test_partial_page(self):
        meta = PaginationMeta.from_params(page=1, limit=10, total=15)
        assert meta.pages == 2  # ceil(15/10) = 2

    def test_exact_pages(self):
        meta = PaginationMeta.from_params(page=1, limit=10, total=30)
        assert meta.pages == 3

    def test_zero_items(self):
        meta = PaginationMeta.from_params(page=1, limit=10, total=0)
        assert meta.pages == 0
        assert meta.has_next is False
        assert meta.has_prev is False

    def test_last_page_has_no_next(self):
        meta = PaginationMeta.from_params(page=5, limit=10, total=50)
        assert meta.pages == 5
        assert meta.has_next is False
        assert meta.has_prev is True

    def test_middle_page_has_both(self):
        meta = PaginationMeta.from_params(page=3, limit=10, total=50)
        assert meta.has_next is True
        assert meta.has_prev is True

    def test_single_page(self):
        meta = PaginationMeta.from_params(page=1, limit=10, total=5)
        assert meta.pages == 1
        assert meta.has_next is False
        assert meta.has_prev is False

    def test_single_item(self):
        meta = PaginationMeta.from_params(page=1, limit=10, total=1)
        assert meta.pages == 1
        assert meta.has_next is False

    def test_large_dataset(self):
        meta = PaginationMeta.from_params(page=1, limit=50, total=10000)
        assert meta.pages == 200
        assert meta.has_next is True
