# SPDX-License-Identifier: AGPL-3.0-or-later
"""History chart columns must be archived into assets_history AND present in
schema-full.sql (#1024).

Migration 136 mirrors os_eol_*/tags/posture_rules into assets_history so the
history_trend reporting charts (Line / Metric Spark / Metric Bar / Chronicle
Bar) can filter and split by OS-EOL, Tag, and Posture fields. Two independent
things must stay in sync, or historical charts break:

1. The pipeline archive step (archive_all_to_history) must copy the columns,
   or every historical asset reads NULL/'{}' for these fields.
2. schema-full.sql must define the columns, because a fresh install bootstraps
   the schema from it and then marks ALL migrations applied WITHOUT running them
   (deploy/lib/migrate.sh bootstrap rule) — so migration 136 never executes on a
   fresh DB, and the archive INSERT would hit an UndefinedColumn on the first run.
"""

import inspect
import re
from pathlib import Path

from src.database import postgres_adapter

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_FULL = _ROOT / "src" / "database" / "schema-full.sql"

_NEW_COLUMNS = (
    "os_eol_status",
    "os_eol_date",
    "os_is_lts",
    "tags",
    "posture_rules",
)


def _archive_column_lists():
    """Return (insert_cols, select_cols) parsed from the assets archive query."""
    src = inspect.getsource(postgres_adapter.PostgreSQLAdapter.archive_all_to_history)
    # `assets_history`, not the sibling `asset_providers_history` INSERT.
    insert_block = re.search(r"INSERT INTO assets_history \((.*?)\)", src, re.S).group(1)
    # The assets leg (SELECT ... FROM assets), not the providers leg.
    select_block = re.search(r"SELECT\s+(.*?)\s+FROM assets\b", src, re.S).group(1)
    insert_cols = [c.strip() for c in insert_block.split(",") if c.strip()]
    select_cols = [c.strip() for c in select_block.split(",") if c.strip()]
    return insert_cols, select_cols


def test_archive_copies_history_chart_columns():
    insert_cols, select_cols = _archive_column_lists()
    for col in _NEW_COLUMNS:
        assert col in insert_cols, f"{col} missing from archive INSERT column list"
        assert col in select_cols, f"{col} missing from archive SELECT list"


def test_archive_insert_select_column_parity():
    """INSERT column count must equal SELECT expression count, or psycopg2
    raises at archive time (INSERT has N columns but N±1 values)."""
    insert_cols, select_cols = _archive_column_lists()
    assert len(insert_cols) == len(select_cols)


def test_schema_full_assets_history_has_chart_columns():
    """Fresh installs bootstrap assets_history from schema-full.sql and never run
    migration 136 (see module docstring), so schema-full.sql must carry these
    columns or the first pipeline archive fails on every fresh install."""
    text = _SCHEMA_FULL.read_text()
    block = re.search(r"CREATE TABLE public\.assets_history \((.*?)\n\);", text, re.S)
    assert block, "assets_history table not found in schema-full.sql"
    body = block.group(1)
    for col in _NEW_COLUMNS:
        assert re.search(rf"\b{re.escape(col)}\b", body), (
            f"{col} missing from assets_history in schema-full.sql"
        )
