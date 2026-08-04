# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Test that in-memory 'is empty' / 'is not empty' checks handle empty lists.

Regression test for GitHub issue #213.
"""

from unittest.mock import Mock

import pytest

from src.modules.tag_rules.services.query_engine_service import QueryEngineService


@pytest.fixture
def engine():
    svc = QueryEngineService.__new__(QueryEngineService)
    return svc


class TestEvaluateConditionEmptyArray:

    def test_empty_list_is_empty(self, engine):
        assert engine._evaluate_condition([], "is empty", "", "tags") is True

    def test_empty_list_is_not_empty(self, engine):
        assert engine._evaluate_condition([], "is not empty", "", "tags") is False

    def test_populated_list_is_empty(self, engine):
        assert engine._evaluate_condition(["a"], "is empty", "", "tags") is False

    def test_populated_list_is_not_empty(self, engine):
        assert engine._evaluate_condition(["a"], "is not empty", "", "tags") is True

    def test_none_is_empty(self, engine):
        assert engine._evaluate_condition(None, "is empty", "", "tags") is True

    def test_none_is_not_empty(self, engine):
        assert engine._evaluate_condition(None, "is not empty", "", "tags") is False
