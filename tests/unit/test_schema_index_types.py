# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify that array columns never use btree indexes.

btree indexes on text[] columns fail when values exceed 2704 bytes
(PostgreSQL's btree row size limit). GIN indexes must be used instead.

This test parses the schema SQL files to catch the problem at CI time,
before it reaches production.

Matching is type-aware: a column only counts as an array when its own
CREATE TABLE declares an array type (``text[]`` etc.). Name-only matching
false-positives on scalar columns that share a name with array columns —
``excluded_records.hostname/ip/mac/sn`` are ``text``/``inet``/``macaddr``,
where btree is correct and ``USING GIN`` is invalid SQL (no default GIN
opclass for scalar types).
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCHEMA_FILES = [
    PROJECT_ROOT / "src" / "database" / "schema.sql",
    PROJECT_ROOT / "src" / "database" / "schema-full.sql",
]

# Columns known to be arrays — extend this list as new array columns are added
ARRAY_COLUMNS = {"hostname", "ip", "mac", "sn", "os", "os_orig"}

# Pattern: CREATE INDEX ... USING btree (column_name)
BTREE_INDEX_RE = re.compile(
    r"CREATE\s+INDEX\s+\w+\s+ON\s+[\w.]+\s+USING\s+btree\s*\((\w+)\)",
    re.IGNORECASE,
)

# Type-aware variants for the schema-file test: capture the table too.
BTREE_INDEX_WITH_TABLE_RE = re.compile(
    r"CREATE\s+INDEX\s+\w+\s+ON\s+(?:ONLY\s+)?([\w.]+)\s+USING\s+btree\s*\((\w+)\)",
    re.IGNORECASE,
)
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)\s*\(",
    re.IGNORECASE,
)
# A column definition line whose type is an array, e.g. "hostname text[],"
ARRAY_COLUMN_DEF_RE = re.compile(r"^\s*(\w+)\s+[^\n,]*\[\]", re.IGNORECASE)


def _array_columns(content: str) -> set[tuple[str, str]]:
    """Return {(table, column)} for columns declared with an array type."""
    result: set[tuple[str, str]] = set()
    lines = content.splitlines()
    in_table = False
    table = ""
    for line in lines:
        if not in_table:
            m = CREATE_TABLE_RE.search(line)
            if m:
                in_table = True
                table = m.group(1).split(".")[-1]
        else:
            if line.startswith(");"):
                in_table = False
                continue
            m = ARRAY_COLUMN_DEF_RE.match(line)
            if m:
                result.add((table, m.group(1)))
    return result


@pytest.mark.parametrize(
    "schema_file",
    [f for f in SCHEMA_FILES if f.exists()],
    ids=lambda f: f.name,
)
def test_no_btree_indexes_on_array_columns(schema_file):
    """Array columns must use GIN indexes, not btree."""
    content = schema_file.read_text()
    array_cols = _array_columns(content)
    violations = []
    for match in BTREE_INDEX_WITH_TABLE_RE.finditer(content):
        table, col = match.group(1).split(".")[-1], match.group(2)
        if (table, col) in array_cols:
            violations.append(f"  {match.group(0)}")
    assert not violations, (
        f"btree indexes found on array columns in {schema_file.name}:\n"
        + "\n".join(violations)
        + "\n\nUse GIN instead: CREATE INDEX ... USING GIN (column)"
    )


def test_migration_files_no_btree_on_arrays():
    """Migration files must not create btree indexes on known array columns."""
    migration_dir = PROJECT_ROOT / "src" / "database" / "migrations"
    if not migration_dir.exists():
        pytest.skip("No migrations directory")
    violations = []
    for sql_file in sorted(migration_dir.glob("*.sql")):
        content = sql_file.read_text()
        for match in BTREE_INDEX_RE.finditer(content):
            col = match.group(1)
            if col in ARRAY_COLUMNS:
                violations.append(f"  {sql_file.name}: {match.group(0)}")
    assert not violations, (
        "btree indexes on array columns found in migrations:\n"
        + "\n".join(violations)
        + "\n\nUse GIN instead: CREATE INDEX ... USING GIN (column)"
    )
