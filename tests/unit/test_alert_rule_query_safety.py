# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests that alert rule SQL execution rejects non-SELECT queries.

Regression test for #184: execute_rule_query ran arbitrary SQL from DB.
"""

import pytest
from unittest.mock import MagicMock

from src.modules.alerts.repository import AlertsRepository


class TestExecuteRuleQuerySafety:
    """execute_rule_query must only allow SELECT statements."""

    @pytest.fixture
    def repo(self):
        adapter = MagicMock()
        return AlertsRepository(adapter)

    def test_valid_select_query(self, repo):
        """Simple SELECT query executes normally."""
        repo.db.execute_query.return_value = [{"id": "abc"}]
        result = repo.execute_rule_query("SELECT id FROM assets WHERE category = 'Server'")
        assert result == [{"id": "abc"}]
        repo.db.execute_query.assert_called_once()

    def test_select_with_cte(self, repo):
        """WITH ... SELECT is allowed."""
        repo.db.execute_query.return_value = []
        sql = "WITH filtered AS (SELECT * FROM assets) SELECT * FROM filtered"
        result = repo.execute_rule_query(sql)
        repo.db.execute_query.assert_called_once()

    def test_rejects_drop_table(self, repo):
        """DROP TABLE must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("DROP TABLE assets")

    def test_rejects_delete(self, repo):
        """DELETE must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("DELETE FROM assets WHERE 1=1")

    def test_rejects_update(self, repo):
        """UPDATE must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("UPDATE users SET role = 'admin'")

    def test_rejects_insert(self, repo):
        """INSERT must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("INSERT INTO users (name) VALUES ('hacker')")

    def test_rejects_truncate(self, repo):
        """TRUNCATE must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("TRUNCATE assets")

    def test_rejects_create(self, repo):
        """CREATE TABLE must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("CREATE TABLE evil (id int)")

    def test_rejects_alter(self, repo):
        """ALTER TABLE must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("ALTER TABLE users ADD COLUMN password text")

    def test_rejects_grant(self, repo):
        """GRANT must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("GRANT ALL ON assets TO public")

    def test_rejects_semicolon_stacked(self, repo):
        """Stacked queries via semicolon must be rejected."""
        with pytest.raises(ValueError, match="semicolon"):
            repo.execute_rule_query("SELECT 1; DROP TABLE assets")

    def test_rejects_copy(self, repo):
        """COPY must be rejected."""
        with pytest.raises(ValueError, match="SELECT"):
            repo.execute_rule_query("COPY users TO '/tmp/dump.csv'")
