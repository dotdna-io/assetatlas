# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard + evaluation + cadence tests for the auth/audit rules seeded in
migration 120 (#584 §17, phase 4).

Mirrors the migration-119 harness (test_ootb_seed_rules.py): the parametrized
guard test reads the migration directly so it covers every auth sql rule without
per-rule maintenance, and the firing tests drive AlertsService with a mocked repo
exactly as the Phase-3 seed-rule tests do.

These rules are all evaluation_cadence='interval': the 5-minute scheduler job
evaluates them, and the pipeline path (get_enabled_rules default cadence
'pipeline') must never pick them up — asserted by test_pack_invisible_to_pipeline.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.modules.alerts.repository import AlertsRepository
from src.modules.alerts.service import AlertsService

_ROOT = Path(__file__).resolve().parents[4]
MIGRATION = _ROOT / 'src' / 'database' / 'migrations' / '120_add_auth_audit_rules.sql'
# auth-privileged-account-created is seeded by migration 125 (#883); auth-admin-role-granted's
# authoritative query was re-authored by migration 133 (subject_id/subject_label projection),
# which supersedes its earlier migration-123 rewrite (#882), and re-authored again by
# migration 145 (#1302), which repointed the rule at audit_trail after auth_audit_log
# was archived and dropped (#1305).
MIGRATION_125 = _ROOT / 'src' / 'database' / 'migrations' / '125_add_privileged_account_created_rule.sql'
MIGRATION_133 = _ROOT / 'src' / 'database' / 'migrations' / '133_add_alert_subject_type.sql'
MIGRATION_145 = _ROOT / 'src' / 'database' / 'migrations' / '145_repoint_auth_role_granted_rule.sql'
SCHEMA_FULL = _ROOT / 'src' / 'database' / 'schema-full.sql'


def _seed_queries(migration=MIGRATION):
    """(slug, query) for every sql-rule query ($q$...$q$) seeded in `migration`."""
    text = migration.read_text()
    pairs = []
    for m in re.finditer(r"\$q\$(.*?)\$q\$", text, re.DOTALL):
        pre = text[:m.start()]
        slugs = re.findall(r"^\s*'([a-z0-9]+(?:-[a-z0-9]+)+)',\s*$", pre, re.MULTILINE)
        pairs.append((slugs[-1] if slugs else '(unknown)', m.group(1)))
    return pairs


# Migration 125 (#883) seeds one additional privileged-account-created rule.
_PRIVILEGED_QUERIES = _seed_queries(MIGRATION_125)


def _reauthored_query(migration, slug):
    text = migration.read_text()
    # (?:(?!\$q\$).)* rather than a bare .*?: migration 133 has three $q$...$q$
    # UPDATEs, and a lazy .*? isn't scoped to one block — re.search anchors on the
    # first 'SET query = $q$' in the file and happily crosses an intervening
    # $q$...$q$ pair when the near closing tag's slug doesn't match, bleeding
    # earlier UPDATEs' bodies into the capture for the 2nd/3rd slug. Refusing to
    # cross a literal $q$ keeps each match scoped to its own dollar-quoted block.
    # The lazy tempered tail after the closing tag spans any extra SET clauses
    # (migration 145 sets updated_at between the query and the WHERE).
    m = re.search(r"SET query = \$q\$((?:(?!\$q\$).)*)\$q\$(?:(?!\$q\$).)*?WHERE slug = '" + re.escape(slug) + "'",
                  text, re.DOTALL)
    return m.group(1) if m else None


def _authoritative_query(slug, seed_query):
    """The query schema-full is expected to carry: the latest re-authoring for
    this slug (migration 145, else migration 133), else migration 120's seed."""
    return (_reauthored_query(MIGRATION_145, slug)
            or _reauthored_query(MIGRATION_133, slug)
            or seed_query)


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    return AlertsService(mock_repo)


def test_migration_120_exposes_nine_sql_seed_queries():
    # Guards against a broken parse turning the parametrized test into a no-op,
    # and pins the pack size so a dropped/added rule is noticed.
    assert len(_seed_queries()) == 9


def test_migration_125_exposes_one_sql_seed_query():
    # Same no-op/parse guard for the migration-125 privileged-account rule (#883).
    assert len(_PRIVILEGED_QUERIES) == 1


@pytest.mark.parametrize('slug,query', _seed_queries() + _PRIVILEGED_QUERIES)
def test_auth_seed_query_passes_select_guard(slug, query):
    """Every seeded auth rule must survive execute_rule_query's SELECT/WITH-only
    guard, or the evaluator would raise on it at runtime and never fire it. The
    guard also rejects stacked queries, so this catches a stray semicolon."""
    repo = AlertsRepository(MagicMock())
    # Raises ValueError if the guard rejects the query; the MagicMock db turns
    # the subsequent execute_query into a harmless no-op.
    repo.execute_rule_query(query)


# =========================================================================
# Firing lifecycle — the SQL does the windowing/thresholding (HAVING >= N),
# so a returned row means the condition already holds; the rule fires on any
# row and resolves when none come back. Mirrors the Phase-3 seed-rule tests.
# =========================================================================

# Two synthetic md5-style uuids standing in for two bursting entities.
_A = '11111111-1111-1111-1111-111111111111'
_B = '22222222-2222-2222-2222-222222222222'


def _auth_rule(slug='auth-failed-login-burst-user', severity='high'):
    return {
        'id': f'rule-{slug}', 'slug': slug, 'name': slug, 'description': 'd',
        'type': 'security', 'severity': severity,
        'query': "SELECT 1", 'threshold': {'min': 1}, 'source': 'system',
        'target_filters': None, 'compliance_criteria': None,
        # None keeps _is_silenced a no-op (silence behaviour is orthogonal and
        # unchanged engine logic; these tests target the fire/resolve lifecycle).
        'silence_duration': None,
    }


def _match(uid, **extra):
    # Only 'id' is load-bearing: the evaluator keys the alert on m['id'] (stored
    # into alerts.asset_ids uuid[]); category/data_source are read with .get()
    # defaults, and no auth rule projects data_source at all.
    row = {'id': uid}
    row.update(extra)
    return row


def test_burst_rule_fires_when_row_returned(service, mock_repo):
    # HAVING count(*) >= 10 lives in SQL; a returned row means the burst exists,
    # so the rule must create an alert.
    mock_repo.execute_rule_query.return_value = [_match(_A, username='alice')]
    mock_repo.get_active_alert_for_rule.return_value = None
    assert service.evaluate_rule(_auth_rule()) == 'created'
    mock_repo.create_alert.assert_called_once()


def test_burst_rule_silent_on_clean_log(service, mock_repo):
    # No rows = no burst in the 15-minute window; with no existing alert the
    # rule stays quiet (no create).
    mock_repo.execute_rule_query.return_value = []
    mock_repo.get_active_alert_for_rule.return_value = None
    assert service.evaluate_rule(_auth_rule()) == 'unchanged'
    mock_repo.create_alert.assert_not_called()


def test_burst_rule_resolves_when_burst_clears(service, mock_repo):
    # The burst aged out of the window; the previously-active alert must resolve.
    mock_repo.execute_rule_query.return_value = []
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'a1', 'asset_ids': [_A]}
    assert service.evaluate_rule(_auth_rule()) == 'resolved'
    mock_repo.resolve_alert.assert_called_once_with('a1')


def test_sustained_burst_updates_one_alert_no_duplicate(service, mock_repo):
    """A burst persisting across two evaluations updates the single active alert
    in place — it never creates a second one."""
    rule = _auth_rule()

    # Tick 1: alice bursting, no existing alert -> exactly one alert created.
    mock_repo.execute_rule_query.return_value = [_match(_A, username='alice')]
    mock_repo.get_active_alert_for_rule.return_value = None
    assert service.evaluate_rule(rule) == 'created'
    assert mock_repo.create_alert.call_count == 1

    # Tick 2: burst persists and grows (bob joins). The existing alert is updated,
    # NOT duplicated — create_alert is still called only once, total.
    mock_repo.execute_rule_query.return_value = [
        _match(_A, username='alice'), _match(_B, username='bob')]
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'a1', 'asset_ids': [_A]}
    assert service.evaluate_rule(rule) == 'updated'
    assert mock_repo.create_alert.call_count == 1
    mock_repo.update_alert_assets.assert_called_once()


def test_sustained_identical_burst_is_unchanged(service, mock_repo):
    """Stable synthetic ids mean an unchanged burst across ticks leaves the alert
    untouched (no needless rewrite): same id set -> 'unchanged'."""
    rule = _auth_rule()
    mock_repo.execute_rule_query.return_value = [_match(_A, username='alice')]
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'a1', 'asset_ids': [_A]}
    assert service.evaluate_rule(rule) == 'unchanged'
    mock_repo.update_alert_assets.assert_not_called()


def test_privileged_account_rule_fires_on_privileged_creation(service, mock_repo):
    """#883: the rule's WHERE already filters to metadata.role IN
    ('admin','super_admin'), so a returned row means a privileged account was just
    created — the rule must alert, same fire-on-any-row lifecycle as its siblings."""
    rule = _auth_rule(slug='auth-privileged-account-created', severity='critical')
    mock_repo.execute_rule_query.return_value = [
        _match(_A, username='eve', granted_role='super_admin')]
    mock_repo.get_active_alert_for_rule.return_value = None
    assert service.evaluate_rule(rule) == 'created'
    mock_repo.create_alert.assert_called_once()


# =========================================================================
# Seed metadata (from the schema-full COPY — the fresh-install source) and the
# pipeline-invisibility regression.
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


# slug -> (severity, enabled, silence_duration) each auth rule must ship with.
# silence_duration is load-bearing: it sets how long the rule stays quiet after
# an alert resolves, so a wrong seed value (e.g. 0, or the wrong unit) silently
# changes re-fire behaviour. Every 24h-window rule silences for 1440 so a
# resolved alert cannot reappear on the next 5-minute tick (PR #824 review).
AUTH_EXPECTED = {
    # migration 132 removed the rest of the pack; auth-admin-role-granted is the
    # only auth/audit rule that ships on fresh installs.
    'auth-admin-role-granted': ('critical', 't', '1440'),
}

# Removed from fresh installs by migration 122 (#877). Migration 120 (immutable)
# still seeds them, so the migration↔schema-full parity accounts for the removal.
REMOVED_BY_122 = {'auth-user-created', 'auth-user-deleted', 'auth-admin-login-off-hours'}

# Added to fresh installs by migration 125 (#883): the privileged-account-created
# rule is dual-written to schema-full but is NOT in migration 120's pack, so the
# migration↔schema-full parity must account for the addition.
ADDED_BY_125 = {'auth-privileged-account-created'}

# Removed from fresh installs AND existing DBs by migration 132: the whole
# auth/audit interval pack except auth-admin-role-granted. Migrations 120/125
# (immutable) still seed these, so the migration↔schema-full parity subtracts them.
REMOVED_BY_132 = {
    'auth-failed-login-burst-user', 'auth-failed-login-burst-ip',
    'auth-refresh-token-reuse', 'auth-mfa-disabled', 'auth-settings-changed',
    'auth-privileged-account-created',
}

# Queries expected byte-identical in the schema-full COPY: migration 120's survivors
# — with auth-admin-role-granted sourced from its latest re-authoring (migration
# 145's audit_trail repoint, #1302, superseding migration 133's subject projection
# and the earlier migration-123 rewrite, #882), which is what schema-full now
# carries — plus the migration-125 privileged-account rule (#883). Skips the rows
# migrations 122 and 132 removed, or the COPY lookup KeyErrors.
_SEED_QUERIES_IN_SCHEMA_FULL = [
    (s, _authoritative_query(s, q))
    for s, q in _seed_queries() if s not in REMOVED_BY_122 and s not in REMOVED_BY_132
] + [(s, q) for s, q in _PRIVILEGED_QUERIES if s not in REMOVED_BY_132]


@pytest.mark.parametrize('slug,expected', sorted(AUTH_EXPECTED.items()))
def test_auth_seed_metadata(slug, expected):
    rows = _copy_alert_rows()
    assert slug in rows, f"{slug} missing from schema-full alert_rules COPY"
    row = rows[slug]
    severity, enabled, silence = expected
    # Every auth rule is a system-owned interval sql rule (path_check requires a
    # query for sql; the cadence is what keeps it off the pipeline path).
    assert row['type'] == 'security'
    assert row['evaluation_type'] == 'sql'
    assert row['evaluation_cadence'] == 'interval'
    assert row['source'] == 'system'
    assert row['severity'] == severity
    assert row['enabled'] == enabled
    assert row['silence_duration'] == silence


def test_pack_invisible_to_pipeline_cadence():
    """The whole pack must be evaluation_cadence='interval'. s20's evaluator calls
    get_enabled_rules(cadence='pipeline'), which filters evaluation_cadence =
    'pipeline', so an interval rule is never evaluated in-pipeline. If any auth
    rule ever landed on 'pipeline' it would silently run every pipeline pass —
    this pins that it cannot."""
    rows = _copy_alert_rows()
    auth_slugs = {s for s in rows if s.startswith('auth-')}
    assert auth_slugs == set(AUTH_EXPECTED), auth_slugs
    pipeline_cadence = {s for s in auth_slugs
                        if rows[s]['evaluation_cadence'] != 'interval'}
    assert pipeline_cadence == set(), (
        f"auth rules must all be interval-cadence; found pipeline: {pipeline_cadence}")


def test_migration_and_schema_full_slugs_agree():
    """Migration 120 seeds nine auth rules; 122 removes three and 125 (#883) adds
    the privileged-account-created rule; then migration 132 removes all but
    auth-admin-role-granted. The schema-full COPY (fresh-install source) must equal
    migration 120's pack minus the 122+132 removals plus the 125 addition."""
    mig_slugs = {slug for slug, _ in _seed_queries()}
    copy_slugs = {s for s in _copy_alert_rows() if s.startswith('auth-')}
    assert (
        ((mig_slugs - REMOVED_BY_122) | ADDED_BY_125) - REMOVED_BY_132
        == copy_slugs
        == set(AUTH_EXPECTED)
    )


def _copy_unescape(s):
    """Inverse of Postgres COPY TEXT escaping — recovers a COPY field's raw text
    (\\n → newline, \\t → tab, \\\\ → backslash)."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            out.append({'n': '\n', 'r': '\r', 't': '\t', '\\': '\\'}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


@pytest.mark.parametrize('slug,query', _SEED_QUERIES_IN_SCHEMA_FULL)
def test_schema_full_query_byte_identical_to_migration(slug, query):
    """The migration promises byte-identical query text dual-written to the
    schema-full COPY (the fresh-install source). The SELECT-guard test above
    reads only the migration, so without this a COPY query that drifted — a bad
    edit or a broken escape — would ship a different or invalid query to fresh
    installs while every other test stayed green."""
    row = _copy_alert_rows()[slug]
    assert _copy_unescape(row['query']) == query


def test_removed_low_signal_rules_absent_from_schema_full():
    """#877: the four low-signal rules must not seed on fresh installs. Auth rules
    live in the alert_rules COPY; presence-flap is a VALUES insert, so assert on the
    raw text for it. auth-admin-role-granted is deliberately retained."""
    rows = _copy_alert_rows()
    for slug in REMOVED_BY_122:
        assert slug not in rows, f"{slug} should be removed from schema-full COPY"
    assert 'auth-admin-role-granted' in rows, "role-granted must be retained"
    text = SCHEMA_FULL.read_text()
    assert "'anomaly-presence-flap'" not in text, "presence-flap must be removed"


def test_migration_132_removed_rules_absent_from_schema_full():
    """migration 132 drops the auth/audit interval pack (all but
    auth-admin-role-granted) from fresh installs; they must not seed via the
    schema-full COPY, while auth-admin-role-granted is retained."""
    rows = _copy_alert_rows()
    for slug in REMOVED_BY_132:
        assert slug not in rows, f"{slug} should be removed from schema-full COPY"
    assert 'auth-admin-role-granted' in rows, "role-granted must be retained"


def test_admin_role_granted_subject_type():
    assert _copy_alert_rows()['auth-admin-role-granted']['subject_type'] == 'audit_event'
