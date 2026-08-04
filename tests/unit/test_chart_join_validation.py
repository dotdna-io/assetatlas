# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for chart JOIN condition validation (allowlist approach).

The join `on` condition must only allow `table.column = table.column` patterns
with validated table/column names. Anything else must be rejected.
"""

import pytest
from pydantic import ValidationError

from src.api.blueprints.charts.models import JoinClause


class TestJoinConditionAllowlist:
    """validate_join_condition must use an allowlist, not a blocklist."""

    def test_valid_simple_join(self):
        """Standard join condition passes."""
        j = JoinClause(table="asset_providers", type="LEFT",
                       on="assets.id = asset_providers.asset_id")
        assert j.on == "assets.id = asset_providers.asset_id"

    def test_valid_reversed_order(self):
        """Reversed join sides also pass."""
        j = JoinClause(table="asset_providers", type="LEFT",
                       on="asset_providers.asset_id = assets.id")
        assert j.on == "asset_providers.asset_id = assets.id"

    def test_rejects_union_select(self):
        """UNION SELECT bypassed the old blocklist — must be rejected."""
        with pytest.raises(ValidationError):
            JoinClause(table="asset_providers", type="LEFT",
                       on="assets.id = asset_providers.asset_id UNION SELECT * FROM users")

    def test_rejects_subquery(self):
        """Subquery in join condition must be rejected."""
        with pytest.raises(ValidationError):
            JoinClause(table="asset_providers", type="LEFT",
                       on="assets.id = (SELECT id FROM users LIMIT 1)")

    def test_rejects_comment_injection(self):
        """Block comment injection must be rejected."""
        with pytest.raises(ValidationError):
            JoinClause(table="asset_providers", type="LEFT",
                       on="assets.id = asset_providers.asset_id /* injected */")

    def test_rejects_unknown_table(self):
        """Join referencing an unknown table in the condition must be rejected."""
        with pytest.raises(ValidationError):
            JoinClause(table="asset_providers", type="LEFT",
                       on="users.id = asset_providers.asset_id")

    def test_allows_unknown_column(self):
        """Column names in the JOIN condition are no longer allowlisted
        (issue #455 / Task 15 — TABLE_FIELDS_MAP retired). The strict
        ``\\w+\\.\\w+`` regex still rejects injection; callers can't read
        a column that isn't exposed by the catalog anyway."""
        j = JoinClause(table="asset_providers", type="LEFT",
                       on="assets.id = asset_providers.password_hash")
        assert j.on == "assets.id = asset_providers.password_hash"

    def test_rejects_bare_expression(self):
        """Bare expression without table.column format must be rejected."""
        with pytest.raises(ValidationError):
            JoinClause(table="asset_providers", type="LEFT",
                       on="1 = 1")

    def test_rejects_truncate(self):
        """TRUNCATE was missing from old blocklist — must be rejected."""
        with pytest.raises(ValidationError):
            JoinClause(table="asset_providers", type="LEFT",
                       on="assets.id = asset_providers.asset_id; TRUNCATE assets")
