# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard + evaluation tests for the OOTB rules seeded in migration 119.

The parametrized guard test reads the migration directly, so it automatically
covers every sql rule added by Phase 3 (posture, system-health, and the six
session-addition rules) without per-rule maintenance.
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.modules.alerts.repository import AlertsRepository
from src.modules.alerts.service import AlertsService

_ROOT = Path(__file__).resolve().parents[4]
MIGRATION = _ROOT / 'src' / 'database' / 'migrations' / '119_add_ootb_rules_and_cadence.sql'
SCHEMA_FULL = _ROOT / 'src' / 'database' / 'schema-full.sql'


def _seed_queries():
    """(slug, query) for every sql-rule query ($q$...$q$) seeded in migration 119."""
    text = MIGRATION.read_text()
    pairs = []
    for m in re.finditer(r"\$q\$(.*?)\$q\$", text, re.DOTALL):
        pre = text[:m.start()]
        slugs = re.findall(r"^\s*'([a-z0-9]+(?:-[a-z0-9]+)+)',\s*$", pre, re.MULTILINE)
        pairs.append((slugs[-1] if slugs else '(unknown)', m.group(1)))
    return pairs


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    return AlertsService(mock_repo)


def test_migration_119_exposes_sql_seed_queries():
    # Guards against a broken parse turning the parametrized test into a no-op.
    assert len(_seed_queries()) >= 2


@pytest.mark.parametrize('slug,query', _seed_queries())
def test_sql_seed_query_passes_select_guard(slug, query):
    """Every seeded sql rule must survive execute_rule_query's SELECT/WITH-only
    guard, or the evaluator would raise on it at runtime and never fire it."""
    repo = AlertsRepository(MagicMock())
    # Raises ValueError if the guard rejects the query; the MagicMock db turns
    # the subsequent execute_query into a harmless no-op.
    repo.execute_rule_query(query)


# --- §3.2 secured-coverage-drop rule: the SQL does the >5% thresholding, so the
#     rule fires on any returned row and resolves when none come back. ---

def _pct_rule():
    return {
        'id': 'rule-32', 'slug': 'posture-tier-drop-secured-to-monitored-pct',
        'name': 'Posture pct', 'description': 'd', 'type': 'security',
        'severity': 'medium', 'query': 'WITH x AS (SELECT 1) SELECT 1',
        'threshold': {'metric': 'secured_to_monitored_pct', 'operator': '>',
                      'value': 5, 'unit': 'percent'},
        'source': 'system', 'target_filters': None,
        'compliance_criteria': None, 'silence_duration': None,
    }


def test_posture_pct_rule_fires_when_snapshot_drop_exceeds_threshold(service, mock_repo):
    # The two-snapshot comparison lives in SQL; a returned row means the drop
    # already exceeded 5%, so the rule must create an alert.
    mock_repo.execute_rule_query.return_value = [
        {'id': '7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d0032', 'category': None},
    ]
    mock_repo.get_active_alert_for_rule.return_value = None
    assert service.evaluate_rule(_pct_rule()) == 'created'


def test_posture_pct_rule_resolves_when_recovered(service, mock_repo):
    # No row = drop within 5% again; the existing alert must resolve.
    mock_repo.execute_rule_query.return_value = []
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'a1', 'asset_ids': ['x']}
    assert service.evaluate_rule(_pct_rule()) == 'resolved'


# =========================================================================
# Seed metadata integrity (from the schema-full COPY — the fresh-install source)
# =========================================================================

_COPY_CACHE = None


def _copy_alert_rows():
    """Parse the alert_rules COPY block into {slug: {column: value}}."""
    global _COPY_CACHE
    if _COPY_CACHE is None:
        lines = SCHEMA_FULL.read_text().splitlines()
        hdr = next(i for i, ln in enumerate(lines)
                   if ln.startswith('COPY public.alert_rules (') and 'FROM stdin;' in ln)
        cols = lines[hdr][lines[hdr].index('(') + 1:lines[hdr].index(')')].split(', ')
        rows = {}
        for ln in lines[hdr + 1:]:
            if ln.startswith('\\.'):
                break
            row = dict(zip(cols, ln.split('\t')))
            rows[row['slug']] = row
        _COPY_CACHE = rows
    return _COPY_CACHE


# (evaluation_type, evaluation_cadence, enabled) each Phase-3 seed must keep.
# Cadence is load-bearing: the 'interval' rules are the ones the scheduler
# worker's 5-minute job evaluates; flipping one silently changes WHERE it runs.
# migration 132 removed assetatlas-update-available, ootb-os-eol-transition,
# ootb-os-eol-forecast-60d and ootb-grace-expiry-preview from fresh installs.
# migration 139 removed the four system-* placeholders: holding those slugs under
# source='system' stopped sync_prometheus_rules taking ownership, so every alert
# Prometheus fired for them was dropped by the webhook. They are recreated as
# source='prometheus' at API boot and are covered by
# test_prometheus_rule_ownership.py instead.
PHASE3_EXPECTED = {
    'posture-tier-drop-secured-to-discovered': ('sql', 'pipeline', 't'),
    'posture-tier-drop-secured-to-monitored-pct': ('sql', 'pipeline', 't'),
    'ootb-security-coverage-floor': ('filter', 'pipeline', 't'),
    'ootb-public-ip-asset': ('sql', 'pipeline', 't'),
    'ootb-chronicle-silence': ('sql', 'pipeline', 't'),
}


MIGRATION_133 = _ROOT / 'src' / 'database' / 'migrations' / '133_add_alert_subject_type.sql'

# slug -> subject_type each Phase-3 seed must ship with (migration 133).
# The four system-* slugs 133 also typed are gone from the seed as of migration
# 139; sync_prometheus_rules now pins their subject_type when it recreates them.
PHASE3_SUBJECT_TYPES = {
    'posture-tier-drop-secured-to-discovered': 'asset',
    'posture-tier-drop-secured-to-monitored-pct': 'metric',
    'ootb-security-coverage-floor': 'asset',
    'ootb-public-ip-asset': 'asset',
    'ootb-chronicle-silence': 'pipeline_run',
    'sync-failures': 'integration',
    'data-source-stale': 'integration',
    'assets-disappeared': 'pipeline_run',
    'stale-assets-retirement': 'asset',
    'auth-admin-role-granted': 'audit_event',
}


def _reauthored_query(migration_path, slug):
    """The dollar-quoted query a migration UPDATEs for `slug`
    (SET query = $q$…$q$ WHERE slug = '<slug>')."""
    # (?:(?!\$q\$).)* rather than a bare .*?: a migration can carry several
    # $q$...$q$ UPDATEs, and a lazy .*? isn't scoped to one block — re.search
    # anchors on the first 'SET query = $q$' in the file and happily crosses an
    # intervening $q$...$q$ pair when the near closing tag's slug doesn't match,
    # bleeding earlier UPDATEs' bodies into the capture for the 2nd/3rd slug.
    # Refusing to cross a literal $q$ keeps each match scoped to its own block.
    m = re.search(r"SET query = \$q\$((?:(?!\$q\$).)*)\$q\$\s*\n\s*WHERE slug = '" + re.escape(slug) + "'",
                  migration_path.read_text(), re.DOTALL)
    assert m, f"no re-authored query for {slug} in {migration_path.name}"
    return m.group(1)


MIGRATION_140 = _ROOT / 'src' / 'database' / 'migrations' / '140_make_data_source_stale_schedule_aware.sql'

# slug -> the migration that LAST re-authored that rule's query. Migration 133
# introduced the subject_id/subject_label/subject_sublabel projection for both;
# migration 140 then rewrote data-source-stale to key off each integration's own
# next_run_at instead of a hardcoded 24-hour window, so 133 is no longer the
# authority for it. Repoint a slug here when a later migration re-authors it —
# never drop one: this map is the only guard keeping the upgrade path (the
# migration) and the fresh-install path (the schema-full COPY) byte-identical.
LAST_REAUTHORED_BY = {
    'sync-failures': MIGRATION_133,
    'data-source-stale': MIGRATION_140,
}


@pytest.mark.parametrize('slug,subject_type', sorted(PHASE3_SUBJECT_TYPES.items()))
def test_phase3_subject_type(slug, subject_type):
    rows = _copy_alert_rows()
    assert slug in rows, f"{slug} missing from schema-full alert_rules COPY"
    assert rows[slug]['subject_type'] == subject_type


def test_last_reauthored_map_covers_both_integration_rules():
    """Guards the guard: a shrunk map would silently stop checking a slug."""
    assert set(LAST_REAUTHORED_BY) == {'sync-failures', 'data-source-stale'}
    for migration in LAST_REAUTHORED_BY.values():
        assert migration.exists(), f"{migration.name} missing"


@pytest.mark.parametrize('slug', sorted(LAST_REAUTHORED_BY))
def test_integration_query_reauthored_in_schema_full(slug):
    from tests.unit.modules.alerts.test_auth_audit_seed_rules import _copy_unescape
    row = _copy_alert_rows()[slug]
    assert _copy_unescape(row['query']) == _reauthored_query(LAST_REAUTHORED_BY[slug], slug)
    assert 'subject_id' in row['query'] and 'subject_label' in row['query']


def test_subject_type_check_constraint_in_schema_full():
    """Migration 133 adds a CHECK constraint on alert_rules.subject_type; the
    schema-full snapshot must carry it too so fresh installs get the same DB-level
    enum guard as migration-upgraded installs. The COPY-block tests only assert
    values, so without this the constraint could silently drop from the fresh-install
    DDL while every other test stayed green."""
    assert 'alert_rules_subject_type_check' in SCHEMA_FULL.read_text()


@pytest.mark.parametrize('slug,expected', sorted(PHASE3_EXPECTED.items()))
def test_phase3_seed_metadata(slug, expected):
    rows = _copy_alert_rows()
    assert slug in rows, f"{slug} missing from schema-full alert_rules COPY"
    row = rows[slug]
    ev_type, cadence, enabled = expected
    assert row['evaluation_type'] == ev_type
    assert row['evaluation_cadence'] == cadence
    assert row['enabled'] == enabled


def test_coverage_floor_uses_resolvable_fieldid_dict():
    """The coverage-floor filter rule must store `field` as a FieldId dict, not
    an encoded string: the compiler's coerce_field_id resolves dicts/FieldIds but
    passes encoded strings through unresolved, silently dropping the filter."""
    row = _copy_alert_rows()['ootb-security-coverage-floor']
    tf = json.loads(row['target_filters'])
    cc = json.loads(row['compliance_criteria'])
    assert isinstance(tf[0]['field'], dict) and tf[0]['field']['kind'] == 'static'
    assert isinstance(cc['conditions'][0]['field'], dict)
    # Inverted (compliance op 'none' + exceeds 5%) so the alert lists the
    # UNCOVERED assets an operator must remediate; coverage < 95% <=> gap > 5%.
    assert cc['conditions'][0]['op'] == 'none'
    assert json.loads(row['threshold']) == {
        'condition': 'exceeds', 'value': 5, 'unit': 'percent'}


def test_interval_rules_match_scheduler_evaluated_set():
    """Exactly the rules the 5-minute scheduler job will pick up (get_enabled_rules
    cadence='interval' → sql/filter only) should carry cadence 'interval'. This is
    a tripwire against a rule silently becoming interval; update the expected set
    deliberately when a phase adds interval rules."""
    rows = _copy_alert_rows()
    interval = {s for s, r in rows.items()
                if r['evaluation_cadence'] == 'interval'
                and r['evaluation_type'] in ('sql', 'filter')
                and r['enabled'] == 't'}
    assert interval == {
        # migration 132 removed the OS-EOL forecast + grace-expiry preview and the
        # rest of the auth/audit interval pack (failed-login bursts, refresh-token
        # reuse, MFA-disabled, settings-changed, privileged-account-created),
        # leaving auth-admin-role-granted as the only interval-cadence rule.
        'auth-admin-role-granted',
        # migration 140 moved data-source-stale off 'pipeline': under that cadence
        # the check only ran during an asset-generation run, so a broken pipeline
        # meant data-source staleness was never checked — and a stale source is a
        # common *cause* of a bad run.
        'data-source-stale',
    }


def test_migration_132_removed_rules_absent_from_schema_full():
    """migration 132 drops four Phase-3 rules from fresh installs; they must not
    reappear in the schema-full COPY (the fresh-install seed source)."""
    rows = _copy_alert_rows()
    for slug in ('assetatlas-update-available', 'ootb-os-eol-transition',
                 'ootb-os-eol-forecast-60d', 'ootb-grace-expiry-preview'):
        assert slug not in rows, f"{slug} should be removed from schema-full COPY"


# =========================================================================
# migration 134: plain-language alert rule copy (alert copy optimization, Task 6).
# =========================================================================

MIGRATION_134 = _ROOT / 'src' / 'database' / 'migrations' / '134_improve_alert_copy.sql'

# slug -> (new_name_or_None, new_description) for the 6 rules whose seed copy lives
# in the schema-full COPY block. None means migration 134 leaves the name alone and
# only rewrites the description.
EXPECTED_COPY = {
    'ootb-public-ip-asset': (
        None,
        'This asset has a public, internet-routable IP address, which may expose it to the internet.'),
    'posture-tier-drop-secured-to-discovered': (
        None,
        "An asset dropped from Secured to Discovered posture in the last 24 hours — it lost the "
        "coverage that had it protected."),
    'ootb-chronicle-silence': (
        'Pipeline Run Recorded No Changes',
        'The latest run found assets but logged no changes, which can mean it silently failed to '
        'track history.'),
    'posture-tier-drop-secured-to-monitored-pct': (
        None,
        'The number of Secured assets fell more than 5% since the previous posture check.'),
    'auth-admin-role-granted': (
        None,
        'A user was granted admin or super-admin access in the last 24 hours.'),
    'ootb-security-coverage-floor': (
        None,
        'More than 5% of servers and workstations have no security tool (endpoint security or '
        'vulnerability management) covering them.'),
}

# slug -> (new_name, new_description) for the 10 anomaly-detection rules whose seed
# copy lives in the schema-full alert_rules INSERT ... VALUES blocks (migrations
# 117/118/121), not the COPY block.
EXPECTED_ANOMALY = {
    'anomaly-mass-new-assets': (
        'Mass New Assets',
        "An unusually large number of new assets appeared this run compared with what's normal "
        "here."),
    'anomaly-mass-missing': (
        'Mass Disappearance',
        'An unusual number of assets stopped being seen across all sources this run.'),
    'anomaly-source-volume': (
        'Source Volume Anomaly',
        'A data source returned far more or far fewer assets than usual, or failed to return a '
        'batch.'),
    'anomaly-attribute-churn': (
        'Attribute Churn',
        'An unusual amount of attribute changes this run — either one field across many assets, '
        'or scattered across many.'),
    'anomaly-segment-outlier': (
        'Out-of-Profile Device in Segment',
        "A new asset doesn't fit the kind of devices that normally live on its network segment."),
    'anomaly-identity-churn': (
        'Identity Churn',
        'An unusual spike in assets being merged, re-added, or replaced this run — a sign of '
        'unstable identity matching.'),
    'anomaly-confidence-shift': (
        'Confidence Drop',
        'More assets than usual now have low-confidence data.'),
    'anomaly-asset-change-burst': (
        'Per-Asset Change Burst',
        'One or more assets changed an unusually large number of fields at once (often a '
        're-image or device replacement).'),
    'anomaly-ml-newcomer': (
        'Unusual New Asset',
        "A newly added asset looks unlike anything else in your environment (its type, OS, "
        "maker, and sources don't match known patterns)."),
    'anomaly-ml-behavior': (
        'Unusual Change Behavior',
        "An asset is changing in a way that doesn't match how similar assets normally change."),
}


@pytest.mark.parametrize('slug,expected', sorted(EXPECTED_COPY.items()))
def test_copy_block_rules_have_plain_language_copy(slug, expected):
    """migration 134: the 6 OOTB rules seeded via the schema-full COPY block carry
    the new plain-language name/description verbatim. Exact-position parse via
    _copy_alert_rows(), same as every other COPY-block assertion in this file."""
    from tests.unit.modules.alerts.test_auth_audit_seed_rules import _copy_unescape
    expected_name, expected_description = expected
    row = _copy_alert_rows()[slug]
    assert _copy_unescape(row['description']) == expected_description
    if expected_name is not None:
        assert _copy_unescape(row['name']) == expected_name


@pytest.mark.parametrize('slug,expected', sorted(EXPECTED_ANOMALY.items()))
def test_anomaly_rules_have_plain_language_copy(slug, expected):
    """migration 134: the 10 anomaly-detection rules seeded via the schema-full
    INSERT ... VALUES blocks (migrations 117/118/121) carry the new plain-language
    name/description. These are SQL string literals, not COPY rows, so the
    assertion is a substring check against the ''-escaped literal text — robust to
    which of the three INSERT blocks the rule lives in and to that block's layout,
    per the task brief ("a substring/contains check ... is acceptable and
    robust")."""
    expected_name, expected_description = expected
    text = SCHEMA_FULL.read_text()
    escaped_name = expected_name.replace("'", "''")
    escaped_description = expected_description.replace("'", "''")
    # Binds name to slug (the tuple's opening two fields) so a match can't be
    # satisfied by the right name text sitting next to some other rule's slug.
    assert f"('{escaped_name}', '{slug}'," in text
    assert f"'{escaped_description}'" in text


def _migration_134_statement(slug):
    """The single `UPDATE ... WHERE slug = '<slug>';` statement for `slug`.
    Splitting migration 134 on ';' is safe here (unlike migration 133's parser,
    which needs the non-crossing (?:(?!\\$q\\$).)* trick): migration 134 has no
    dollar-quoted $q$...$q$ bodies, and none of its 16 name/description values
    contain a literal ';', so a plain split can't bleed one slug's UPDATE into
    another's."""
    text = MIGRATION_134.read_text()
    for stmt in text.split(';'):
        if f"WHERE slug = '{slug}'" in stmt:
            return stmt
    return None


@pytest.mark.parametrize('slug,expected', sorted({**EXPECTED_COPY, **EXPECTED_ANOMALY}.items()))
def test_migration_134_matches_schema_full(slug, expected):
    """Drift guard: migration 134 must UPDATE every one of the 16 slugs with the
    same new description dual-written to schema-full (COPY block or INSERT block),
    or a fresh install and a migration-upgraded install would silently end up with
    different copy for the same rule."""
    _, expected_description = expected
    stmt = _migration_134_statement(slug)
    assert stmt is not None, f"migration 134 has no UPDATE for slug {slug!r}"
    escaped_description = expected_description.replace("'", "''")
    assert f"'{escaped_description}'" in stmt, (
        f"migration 134's description for {slug!r} drifted from schema-full")


def test_expected_copy_and_anomaly_cover_sixteen_slugs():
    # Guards against a typo'd/duplicated key silently shrinking the parametrized
    # coverage below the 16 rules the task brief's copy table lists.
    assert len(EXPECTED_COPY) == 6
    assert len(EXPECTED_ANOMALY) == 10
    assert not set(EXPECTED_COPY) & set(EXPECTED_ANOMALY)


# =========================================================================
# migration 137: 'assets-disappeared' measures the asset POPULATION, not the
# per-run creation velocity. It compared stats->>'total_assets_created' (chronicle
# asset_created + asset_readded, set in s18_change_tracking — 0-2 in steady state)
# between the two latest runs, so a 1->0 tick read as a 100% drop and the rule
# fired while the real population never moved. The fix reads stats->>'total_assets'
# (len(result_df), the population). See migration 137's header for the full story.
# =========================================================================

MIGRATION_137 = _ROOT / 'src' / 'database' / 'migrations' / '137_fix_assets_disappeared_metric.sql'


def _migration_137_query():
    """migration 137's re-authored query for slug 'assets-disappeared'."""
    return _reauthored_query(MIGRATION_137, 'assets-disappeared')


def test_assets_disappeared_thresholds_population_not_creation_velocity():
    """Regression guard for the false-positive fix (migration 137). The
    'assets-disappeared' rule must threshold the run-over-run drop of the asset
    POPULATION (stats->>'total_assets'), NOT the per-run created/re-added count
    (stats->>'total_assets_created'). That count is 0-2 in steady state, so any
    tick (1->0) reads as a >10% drop and the rule fires while nothing disappeared.
    Flip the field back and this test fails."""
    from tests.unit.modules.alerts.test_auth_audit_seed_rules import _copy_unescape
    q = _copy_unescape(_copy_alert_rows()['assets-disappeared']['query'])
    assert 'total_assets_created' not in q, (
        "rule reads the creation-velocity field again -> false-fires on normal "
        "new-asset fluctuation with a flat population")
    # Population is read for both the current run and the LAG(previous run)...
    assert "(stats->>'total_assets')::int as total_assets" in q
    assert "LAG((stats->>'total_assets')::int)" in q
    # ...and the >10% run-over-run drop arithmetic is preserved.
    assert "(r.prev_total - r.total_assets)::float / r.prev_total > 0.10" in q


def test_migration_137_matches_schema_full():
    """Drift guard: migration 137 (upgrade path) and the schema-full COPY
    (fresh-install path) must ship byte-identical query text for
    'assets-disappeared', or the two install kinds would evaluate it differently."""
    from tests.unit.modules.alerts.test_auth_audit_seed_rules import _copy_unescape
    row = _copy_alert_rows()['assets-disappeared']
    assert _copy_unescape(row['query']) == _migration_137_query()
