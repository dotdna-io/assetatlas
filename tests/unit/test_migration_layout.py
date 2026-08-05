# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static checks on src/database/migrations/.

Catches release-readiness regressions without spinning up Postgres:
- 001 / 002 are intentionally absent — the bootstrap path is schema-full.sql.
- 003+ filenames must follow the NNN_verb_description.sql shape so
  migrate.sh's lexical-order scan is deterministic.
- Files without `BEGIN;` / `COMMIT;` (or alternative transactional control
  like `BEGIN ATOMIC`) are flagged so reviewers consider whether atomicity
  is needed before merging.

Existing duplicate prefixes (e.g. two 003_* files, three 052_* files) are
allow-listed; renaming would break installed databases that key
schema_migrations on filename. New duplicates are rejected so the list
doesn't grow.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "src" / "database" / "migrations"

# Filenames must look like NNN[a-z]?_verb_description.sql. The optional
# lowercase suffix (e.g. 045b_, 045c_) disambiguates files that share a
# numeric prefix without renaming the original applied migration.
FILENAME_PATTERN = re.compile(r"^(?P<num>\d{3})(?P<sfx>[a-z]?)_[a-z0-9_]+\.sql$")

# Numeric prefixes that have legitimately drifted into duplicates over the
# project's life. Frozen so the next contributor can't add to the list:
# renaming them would change the schema_migrations PK on every existing
# installation and silently re-apply the migration.
# "103" joined in 2026-05: three PRs (#646/#647/#648) raced the same number
# within 13 hours and all shipped in v2026.19.19/v2026.19.20, so the trio
# (103_add_custom_subcategories, 103_add_oidc_post_logout_redirect_uri,
# 103_add_asset_label_overrides) is keyed by filename in the wild.
ALLOWED_DUPLICATES = {"003", "007", "014", "052", "053", "064", "095", "099", "103"}


def _migration_files():
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def test_migrations_dir_exists():
    assert MIGRATIONS_DIR.is_dir(), f"{MIGRATIONS_DIR} not found"


def test_001_and_002_intentionally_absent():
    """schema-full.sql is the canonical bootstrap; 001/002 do not exist as
    standalone migrations. If someone re-introduces them, that contradicts
    the bootstrap-from-snapshot design and should be flagged."""
    files = {p.name for p in _migration_files()}
    leading_001 = [n for n in files if n.startswith("001_")]
    leading_002 = [n for n in files if n.startswith("002_")]
    assert not leading_001, (
        f"Migrations starting with 001_ are reserved for the schema-full.sql "
        f"snapshot path; saw {leading_001}. See deploy/lib/migrate.sh."
    )
    assert not leading_002, (
        f"Migrations starting with 002_ are reserved for the schema-full.sql "
        f"snapshot path; saw {leading_002}."
    )


def test_filenames_match_naming_convention():
    bad = [p.name for p in _migration_files() if not FILENAME_PATTERN.match(p.name)]
    assert not bad, (
        "Migration filenames must match NNN[a-z]?_verb_description.sql:\n  "
        + "\n  ".join(bad)
    )


def test_no_new_duplicate_number_prefixes():
    """Two files sharing the same numeric prefix without a disambiguating
    letter suffix is a real duplicate. Files like 045_ + 045b_ + 045c_ are
    fine — the suffix gives them distinct lex-order keys.
    """
    counts = {}  # numeric prefix -> count of files lacking a letter suffix
    for p in _migration_files():
        m = FILENAME_PATTERN.match(p.name)
        if not m:
            continue
        if m.group("sfx"):
            continue  # suffixed file, deterministic
        prefix = m.group("num")
        counts[prefix] = counts.get(prefix, 0) + 1

    new_dupes = {n for n, c in counts.items() if c > 1} - ALLOWED_DUPLICATES
    assert not new_dupes, (
        "New duplicate migration number(s) detected: "
        f"{sorted(new_dupes)}.\n"
        "Renaming an existing migration breaks installs that track applied "
        "migrations by filename. Pick the next free number, or add a letter "
        "suffix (e.g. NNNb_) if you intentionally want to wedge between."
    )


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_migration_uses_explicit_transaction(path):
    """Migrations should wrap statements in BEGIN; ... COMMIT; or be a single
    DO $$ ... $$ block. CLAUDE.md mandates idempotent, transactional
    migrations; surface the few that drift so reviewers consider whether
    atomicity matters before approving."""
    text = path.read_text().upper()
    if "BEGIN" in text and "COMMIT" in text:
        return
    if "BEGIN ATOMIC" in text:
        return
    if path.name in _ALLOWED_NON_TRANSACTIONAL:
        return
    pytest.fail(
        f"{path.name} has no BEGIN/COMMIT pair. Either wrap statements in "
        "an explicit transaction or add the filename to the allow-list with "
        "a justification."
    )


# Historical migrations that ship without an explicit BEGIN/COMMIT pair.
# Postgres wraps each migration's psql session in an implicit transaction
# already, so most of these still apply atomically; the audit (BLOCKER-26
# of #596) flagged them because reviewers couldn't tell at a glance. The
# list is frozen — new migrations must use explicit BEGIN/COMMIT or
# justify joining the allow-list.
_ALLOWED_NON_TRANSACTIONAL: set[str] = {
    "003_create_excluded_records_table.sql",
    "007_remove_is_risky_os_eol.sql",
    "008_create_invalid_assets.sql",
    "011_add_generation_run_id_to_chronicle.sql",
    "012_consolidated_chronicle_schema.sql",
    "013_drop_legacy_chronicle_fields.sql",
    "014_add_lifecycle_columns.sql",
    "014_add_os_enrichment_jsonb.sql",
    "015_drop_invalid_assets_table.sql",
    "020_add_category_prediction_columns.sql",
    "021_add_os_eol_tracking.sql",
    "022_enhance_mac_vendor_ranges.sql",
    "023_add_os_category_to_providers.sql",
    "024_authentication_schema.sql",
    "025_create_super_admin.sql",
    "026_add_schedule_fields.sql",
    "027_add_data_storage_tables.sql",
    "028_add_connection_test_fields.sql",
    "029_add_transformations_field.sql",
    "030_add_job_execution_history.sql",
    "031_reporting_persistence.sql",
    "032_report_workflow.sql",
    "033_add_fk_cascade_indexes.sql",
    "034_add_ip_primary.sql",
    "035_add_category_ml_columns.sql",
    "036_create_pipeline_runs.sql",
}
