# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static checks on migration 141 (`pipeline_runs.data_settled_at`, #1185).

The column exists because `completed_at` is CONTESTED: the worker stamps it in
`_execute_run`, and `pipeline_watchdog.recover_orphaned_running_runs` stamps it
too on any row still 'running' past the recovery window — including a run that is
merely slow and whose worker is still writing. `get_data_version` used to order on
it, so a slow-but-alive run bumped the data version mid-rebuild and, because the
worker's terminal UPDATE is deliberately guarded on `status IN ('pending',
'running')`, was never corrected afterwards. `data_settled_at` is written by the
worker alone, in the `finally` that already flushes the dashboard cache.

Three things are guarded here:

1. The migration itself -- idempotent, transactional, doesn't self-record, and
   carries the backfill without which every existing install answers all-nulls
   until its next run finishes (same shape of assertions as
   test_migration_135_denylist_patterns.py).

2. The schema-full.sql dual-write. A fresh install bootstraps from
   schema-full.sql and then marks every migration applied WITHOUT running it
   (deploy/lib/migrate.sh bootstrap_if_empty), so a column that exists only in
   the migration is silently missing on every fresh install -- forever, because
   apply_migrations then skips 141 for good.

3. The stamp block's comma separators. test_schema_full_migration_stamp.py owns
   the "is 141 listed" half and runs in the hard gate; what it cannot see is
   whether the list is still valid SQL (see the test below).
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "src/database/migrations/141_add_pipeline_runs_data_settled_at.sql"
_SCHEMA_FULL = _ROOT / "src/database/schema-full.sql"


def _migration_sql() -> str:
    return _MIGRATION.read_text()


def _pipeline_runs_ddl() -> str:
    """The CREATE TABLE public.pipeline_runs block from schema-full.sql."""
    sql = _SCHEMA_FULL.read_text()
    start = sql.index("CREATE TABLE public.pipeline_runs")
    return sql[start:sql.index("\n);", start)]


def _stamp_block() -> str:
    """The bootstrap `INSERT INTO public.schema_migrations ... VALUES (...)` list."""
    sql = _SCHEMA_FULL.read_text()
    start = sql.index("INSERT INTO public.schema_migrations (filename) VALUES")
    return sql[start:sql.index("ON CONFLICT DO NOTHING;", start)]


def test_migration_exists_and_is_idempotent():
    sql = _migration_sql()
    assert "ADD COLUMN IF NOT EXISTS data_settled_at timestamptz" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_pipeline_runs_settled" in sql
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    # Must NOT self-record -- deploy/lib/migrate.sh owns schema_migrations.
    assert "schema_migrations" not in sql


def test_existing_runs_are_backfilled_from_completed_at():
    """REQUIRED, not cosmetic. `get_data_version` filters on `data_settled_at IS
    NOT NULL`, so without the backfill every install that already has run history
    answers all-nulls the moment 141 lands -- no tab learns anything until the
    next run settles, which on the default 24h cadence is a whole day of every
    client believing the asset table has never been rebuilt.

    `completed_at` is the right source for old rows: it is contested going
    forward, but for a run that has already finished it is the only record of
    when its data landed.
    """
    sql = _migration_sql()
    assert "UPDATE pipeline_runs SET data_settled_at = completed_at" in sql


def test_the_backfill_is_re_runnable():
    """`data_settled_at IS NULL` is what makes a second run a no-op instead of
    resetting a stamp the worker has since written. `completed_at IS NOT NULL`
    keeps the in-flight row out of it -- backfilling a pending/running run would
    hand the version signal a run whose rebuild has not happened yet, which is
    the very failure 141 exists to stop."""
    sql = _migration_sql()
    assert "WHERE completed_at IS NOT NULL AND data_settled_at IS NULL" in sql


def test_the_index_is_partial_and_descending():
    """`get_data_version` is polled once a minute by every open tab and asks for
    exactly one row: `WHERE data_settled_at IS NOT NULL ORDER BY data_settled_at
    DESC LIMIT 1`. Before this, pipeline_runs was indexed on created_at,
    started_at and status only, so that read was a sort over the whole table
    (#1185 finding 3). The partial predicate matches the query's own filter and
    keeps unsettled rows out of the index entirely."""
    sql = _migration_sql()
    assert "ON pipeline_runs (data_settled_at DESC)" in sql
    assert "WHERE data_settled_at IS NOT NULL" in sql


def test_schema_full_declares_the_column():
    ddl = _pipeline_runs_ddl()
    assert "data_settled_at timestamp with time zone" in ddl


def test_schema_full_declares_the_index():
    sql = _SCHEMA_FULL.read_text()
    assert (
        "CREATE INDEX idx_pipeline_runs_settled ON public.pipeline_runs "
        "USING btree (data_settled_at DESC) WHERE (data_settled_at IS NOT NULL);"
    ) in sql


def test_the_stamp_block_keeps_its_comma_separators():
    """The parity gate finds filenames by regex, so it is just as happy with

        ('140_make_data_source_stale_schedule_aware.sql')
        ('141_add_pipeline_runs_data_settled_at.sql')

    -- a VALUES list with no separator, which psql rejects outright and which
    would break the whole fresh-install bootstrap. Appending an entry means
    adding a comma to the line above it, and nothing else in the suite notices
    if you forget.
    """
    block = _stamp_block()
    entries = re.findall(r"\('[^']+'\)", block)
    assert len(entries) > 100, f"stamp block looks truncated: {len(entries)} entries"
    assert block.count("'),") == len(entries) - 1, (
        "every entry in the stamp block except the last must be followed by a "
        f"comma: {len(entries)} entries but {block.count(chr(39) + '),')} separators"
    )
