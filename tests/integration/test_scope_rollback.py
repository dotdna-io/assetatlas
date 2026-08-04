# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import uuid
import pytest
from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter

pytestmark = pytest.mark.integration


@pytest.fixture
def adapter():
    url = os.getenv("TEST_DATABASE_URL", "postgresql://harness:harness@localhost:5440/assetatlas_harness")
    cm = PostgreSQLConnectionManager(database_url=url)
    a = PostgreSQLAdapter(cm)
    a.execute_query("CREATE TABLE IF NOT EXISTS _scope_probe (id uuid primary key)", (), fetch=False)
    a.execute_query("TRUNCATE _scope_probe", (), fetch=False)
    yield a
    a.execute_query("DROP TABLE IF EXISTS _scope_probe", (), fetch=False)


def test_scope_rolls_back_all_writes_on_late_failure(adapter):
    cm = adapter.conn_manager
    rid = str(uuid.uuid4())
    cm.begin_scope()
    try:
        adapter.execute_query("INSERT INTO _scope_probe (id) VALUES (%s::uuid)", (rid,), fetch=False)
        raise RuntimeError("late-stage failure")
    except RuntimeError:
        cm.end_scope(error=True)
    # New connection (no scope) must NOT see the insert — it was rolled back.
    rows = adapter.execute_query("SELECT count(*) AS n FROM _scope_probe WHERE id = %s::uuid", (rid,))
    assert rows[0]["n"] == 0


def test_scope_commits_all_writes_on_success(adapter):
    cm = adapter.conn_manager
    rid = str(uuid.uuid4())
    cm.begin_scope()
    adapter.execute_query("INSERT INTO _scope_probe (id) VALUES (%s::uuid)", (rid,), fetch=False)
    cm.end_scope()
    rows = adapter.execute_query("SELECT count(*) AS n FROM _scope_probe WHERE id = %s::uuid", (rid,))
    assert rows[0]["n"] == 1
