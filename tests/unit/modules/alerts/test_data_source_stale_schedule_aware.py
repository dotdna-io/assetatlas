# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration 140: the 'data-source-stale' rule fires on two independent signals —
each integration's own schedule, plus a schedule-blind sync-age backstop —
instead of a single hardcoded 24-hour window.

The old query fired on any enabled integration whose last_sync_at was older than a
literal `interval '24 hours'`. With sources scheduled roughly daily that window
equalled the sync period, so a sync slipping by minutes tripped a `high` alert (all
34 enabled integrations on the shared dev DB matched at once), while an hourly
source could be dead for 23 hours in silence.

These are static assertions over the migration text and the schema-full COPY row —
the same level the other seeded-rule guards in this package work at, since unit
tests run without Postgres. The semantics were verified separately by running the
query read-only against the shared dev DB, both over the live integrations table
(20 matches before, 34 after, including all 14 month-stale sources that a healed
next_run_at had made invisible) and over a synthetic VALUES list substituted for
`FROM integrations i`, which is what pins the day-zero and backstop boundaries:

    created now, no next_run_at, no last_sync_at      -> quiet
    created 59 min ago, same                          -> quiet
    created 61 min ago, same                          -> MATCH (past due)
    created_at NULL, same                             -> MATCH (terminal -infinity)
    future next_run_at, synced 47 h ago               -> quiet
    future next_run_at, synced 49 h ago               -> MATCH (sync-age backstop)
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.api.modules.settings.settings_schema import APPLICATION_SETTINGS
from src.modules.alerts.repository import AlertsRepository

_ROOT = Path(__file__).resolve().parents[4]
MIGRATION_140 = _ROOT / 'src' / 'database' / 'migrations' / '140_make_data_source_stale_schedule_aware.sql'
SCHEMA_FULL = _ROOT / 'src' / 'database' / 'schema-full.sql'

SETTING_CATEGORY = 'alerting'

# key -> (default, min, max, the interval unit the query appends before ::interval).
# One entry per branch of the rule; the tests below are parametrized over both, so
# a third window cannot be added without its schema/seed/dual-write guards.
SETTINGS = {
    'DATA_SOURCE_STALE_GRACE_MINUTES': (60, 5, 10080, 'minutes'),
    'DATA_SOURCE_MAX_SYNC_AGE_HOURS': (48, 1, 8760, 'hours'),
}
GRACE_KEY = 'DATA_SOURCE_STALE_GRACE_MINUTES'
SYNC_AGE_KEY = 'DATA_SOURCE_MAX_SYNC_AGE_HOURS'

# The two branch anchors, verbatim. Each appears exactly twice in the query: once
# in its WHERE branch, once gating the sublabel arm that names that branch.
PAST_DUE_ANCHOR = "COALESCE(i.next_run_at, i.last_sync_at, i.created_at, '-infinity'::timestamptz)"
SYNC_AGE_ANCHOR = "COALESCE(i.last_sync_at, i.created_at, '-infinity'::timestamptz)"


def _migration_text():
    return MIGRATION_140.read_text()


def _query():
    """Migration 140's re-authored query for 'data-source-stale'.

    Same non-$q$-crossing capture the other seed guards use, so a second
    dollar-quoted block added to this migration later can't bleed into the match.
    """
    m = re.search(
        r"SET query = \$q\$((?:(?!\$q\$).)*)\$q\$\s*\n\s*WHERE slug = 'data-source-stale'",
        _migration_text(), re.DOTALL)
    assert m, "migration 140 has no re-authored query for 'data-source-stale'"
    return m.group(1)


def _copy_row():
    """The 'data-source-stale' row of the schema-full alert_rules COPY block."""
    lines = SCHEMA_FULL.read_text().splitlines()
    hdr = next(i for i, ln in enumerate(lines)
               if ln.startswith('COPY public.alert_rules (') and 'FROM stdin;' in ln)
    cols = lines[hdr][lines[hdr].index('(') + 1:lines[hdr].index(')')].split(', ')
    for ln in lines[hdr + 1:]:
        if ln.startswith('\\.'):
            break
        row = dict(zip(cols, ln.split('\t')))
        if row['slug'] == 'data-source-stale':
            return row
    pytest.fail("'data-source-stale' missing from the schema-full alert_rules COPY")


def _settings_copy_row(key):
    """`key`'s row of the schema-full system_settings COPY block, or None."""
    lines = SCHEMA_FULL.read_text().splitlines()
    hdr = next(i for i, ln in enumerate(lines)
               if ln.startswith('COPY public.system_settings (') and 'FROM stdin;' in ln)
    cols = lines[hdr][lines[hdr].index('(') + 1:lines[hdr].index(')')].split(', ')
    for ln in lines[hdr + 1:]:
        if ln.startswith('\\.'):
            break
        row = dict(zip(cols, ln.split('\t')))
        if row['key'] == key:
            return row
    return None


def _sublabel_expression():
    """The subject_sublabel projection, from concat_ws to its alias."""
    q = _query()
    return q[q.index('concat_ws('):q.index('AS subject_sublabel')]


def _where_clause():
    """Everything from WHERE to the end of the query."""
    q = _query()
    return q[q.index('WHERE i.status'):]


def _grace_subquery(key, default):
    """The COALESCE'd scalar-subquery read of `key`, as a regex."""
    return (r"COALESCE\(\(SELECT s\.value::int\s+FROM system_settings s\s+"
            r"WHERE s\.category = 'alerting'\s+AND s\.key = '%s'\s+"
            r"AND trim\(s\.value\) ~ '\^\[0-9\]\{1,6\}\$'\), %d\)" % (key, default))


# =========================================================================
# The query itself
# =========================================================================

def test_query_passes_select_guard():
    """execute_rule_query allows only SELECT / WITH ... SELECT and rejects stacked
    statements. The parametrized guard in test_ootb_seed_rules.py walks migration
    119 only, so a rule re-authored by a later migration needs its own case — this
    query would otherwise raise ValueError at evaluation time and never fire."""
    repo = AlertsRepository(MagicMock())
    # Raises ValueError if the guard rejects it; the MagicMock db makes the
    # subsequent execute_query a harmless no-op.
    repo.execute_rule_query(_query())


def test_query_has_no_hardcoded_staleness_interval():
    """The whole point of the change: both windows must come from settings, not
    from literals baked into SQL an operator cannot edit (system-owned rules raise
    SystemRuleImmutableError on update).

    The lookbehind excludes `::interval`, which is the *cast* applied to the
    settings-derived string — that one is required, an `interval 'N units'` literal
    is what must not come back."""
    q = _query()
    literals = re.findall(r"(?<!:)\binterval\s+'[^']*'", q, re.IGNORECASE)
    assert not literals, f"hardcoded interval literal(s) survive in the query: {literals}"
    assert '24 hours' not in q and '24h' not in q


@pytest.mark.parametrize('key', sorted(SETTINGS))
def test_query_reads_each_window_from_its_setting_with_coalesce_literal_fallback(key):
    """Read as a scalar subquery wrapped in COALESCE, NOT as a CTE cross-joined to
    integrations: migration 119's ootb-grace-expiry-preview uses the cross-join
    form, which silently yields zero rows — and therefore never fires — when the
    settings row is absent. A scalar subquery degrades to the default instead."""
    default, _, _, unit = SETTINGS[key]
    q = _query()
    assert f"s.key = '{key}'" in q
    assert f"s.category = '{SETTING_CATEGORY}'" in q
    assert 'FROM system_settings s' in q
    assert re.search(_grace_subquery(key, default), q), \
        f"{key} is not read as a COALESCE'd scalar subquery with a literal default"
    # ...and the value is turned into an interval in that setting's own unit, so
    # the name and the arithmetic cannot drift apart.
    assert re.search(_grace_subquery(key, default) + r"\s*\|\| ' %s'\)::interval" % unit, q), \
        f"{key} is not applied as an interval in {unit}"
    # No cross-joined settings CTE: that is the zero-rows trap this rule must avoid.
    assert not re.search(r'FROM\s+integrations\s+\w*\s*,', q), \
        "integrations is cross-joined to something — the zero-rows trap"


def test_every_settings_cast_is_guarded_against_a_malformed_value():
    """COALESCE only rescues a MISSING row. system_settings.value is TEXT and the
    installer scripts write the table directly via psql, so '' or 'abc' is storable —
    and a bare s.value::int would raise, execute_rule_query would propagate, and
    evaluate_rule swallows that as 'error'. The rule would then be permanently dead
    with any already-active alert never resolved: the same silent failure the
    COALESCE exists to prevent, one row-state over.

    Filtering inside the subquery makes a malformed value simply not match, so the
    COALESCE falls back to the default. The {1,6} bound also keeps the ::int cast
    away from an out-of-range literal (the higher of the two ceilings is 10080).

    Asserted as a count rather than a presence check: with two settings read in two
    places each, a single unguarded cast is enough to kill the rule."""
    q = _query()
    casts = q.count('s.value::int')
    guards = q.count("trim(s.value) ~ '^[0-9]{1,6}$'")
    assert casts == guards, \
        f'{casts} settings casts but only {guards} regex guards — an unguarded ::int'
    assert casts == 4, \
        'expected each of the two settings to be read twice (WHERE branch + sublabel arm)'
    # Digit class rather than \d: the query is dual-written into a COPY block, where
    # a backslash is an escape character. Keeping it backslash-free removes that trap.
    assert '\\' not in q, "backslash in the query text — COPY escaping hazard"


def test_query_keys_off_each_integrations_own_due_time():
    """Branch 1. next_run_at is the scheduler's per-integration 'due at' pointer, so
    this window is relative to that source's own schedule rather than a global age —
    it catches an hourly source within its own grace window instead of making it wait
    out a fixed one."""
    where = _where_clause()
    assert 'i.next_run_at' in where
    assert re.search(re.escape(PAST_DUE_ANCHOR) + r'\s*<\s*\n?\s*now\(\)\s*-', where), \
        'the past-due branch no longer compares the next_run_at anchor against now()'


def test_query_excludes_unscheduled_integrations():
    """schedule_enabled = false means manual/upload-driven with no declared refresh
    expectation. Those alerted forever under the old rule and were its single
    largest false-positive source. Applies to BOTH branches — the sync-age backstop
    must not resurrect them."""
    where = _where_clause()
    assert 'AND i.schedule_enabled' in where
    # The guard sits outside the OR, so neither branch can bypass it.
    assert where.index('AND i.schedule_enabled') < where.index('AND (')


def test_query_excludes_integrations_with_a_run_in_flight():
    """next_run_at is advanced only in _execute_integration_robust's finally, AFTER
    every JobExecutor attempt (SCHEDULER_JOB_TIMEOUT 3600s x SCHEDULER_MAX_RETRIES 3),
    and last_sync_at is written only once a sync finishes — so an integration looks
    both past-due AND unrefreshed for the whole duration of its own run. Without this
    guard a sync that outlives either window — a slow full sync, or one whose first
    attempt hits the 1-hour timeout and is retried — fires a `high` alert while it is
    actively refreshing.

    Trusting the flag is safe: one stranded by a crashed process is cleared at
    API/scheduler startup by cleanup_stale_running_flags, which probes the advisory
    lock run_integration actually holds rather than believing the flag."""
    where = _where_clause()
    assert 'AND NOT i.is_running' in where
    assert where.index('AND NOT i.is_running') < where.index('AND (')


# =========================================================================
# Fix 1 — a brand-new scheduled integration must not fire on day zero
# =========================================================================

def test_past_due_anchor_falls_through_created_at_before_infinity():
    """Each element of the chain covers a state the one before it cannot see:

      * next_run_at IS NULL on a *scheduled* integration is itself a fault — the
        scheduler never computed a due time — so the chain keeps walking rather than
        treating NULL as healthy.
      * last_sync_at covers a re-enabled integration: it has synced before.
      * created_at covers a JUST-CREATED one, which has neither. next_run_at is
        populated asynchronously (update_integration only signals the scheduler; the
        10-second dirty-schedule check computes it, 2-minute resync as fallback) while
        the interval alert job evaluates every 5 minutes — so without this element a
        brand-new integration fires `high` and dispatches outbound deliveries before
        the source has had any chance to sync. With the scheduler worker down, 100%
        of the time.
      * '-infinity' must STAY as the terminal element: integrations.created_at is
        nullable (DEFAULT now(), no NOT NULL), so dropping it would make a row with a
        NULL created_at silently un-alertable — failing open on exactly the malformed
        row most likely to need attention."""
    q = _query()
    assert PAST_DUE_ANCHOR in q, 'the past-due fallback chain is missing or reordered'
    # Order matters, and so does the terminal element.
    assert re.search(r"COALESCE\(\s*i\.next_run_at\s*,\s*i\.last_sync_at\s*,"
                     r"\s*i\.created_at\s*,\s*'-infinity'::timestamptz\s*\)", q)
    assert q.count(PAST_DUE_ANCHOR) == 2, \
        'the past-due anchor should appear once in WHERE and once gating its sublabel arm'


def test_sync_age_anchor_also_falls_through_created_at():
    """The backstop branch needs the same day-zero guard. A plain
    `last_sync_at IS NULL OR last_sync_at < now() - window` would match a
    never-synced brand-new integration immediately and re-fire the exact alert the
    past-due branch's created_at anchor exists to prevent — the two branches are
    OR'd, so a day-zero match on either one is a day-zero alert.

    Anchoring on created_at keeps 'never synced' catchable (once the source has had
    a full window to sync) without making it instant, and keeps '-infinity' as the
    terminal element for the same nullable-created_at reason as branch 1."""
    q = _query()
    assert SYNC_AGE_ANCHOR in q, 'the sync-age branch has no created_at fallback'
    assert q.count(SYNC_AGE_ANCHOR) == 2, \
        'the sync-age anchor should appear once in WHERE and once gating its sublabel arm'
    # The naive form is what must NOT come back.
    assert not re.search(r'i\.last_sync_at\s+IS\s+NULL\s+OR', _where_clause(), re.IGNORECASE), \
        'bare `last_sync_at IS NULL OR ...` re-fires the day-zero alert'


# =========================================================================
# Fix 2 — a healed next_run_at must not silence a genuinely dead source
# =========================================================================

def test_where_clause_ors_the_two_branches():
    """next_run_at answers 'did the scheduler service the job?', not 'did the data
    actually refresh?' — and those diverge exactly when something is wrong.
    _recover_schedules re-creates every scheduled job at worker start and
    _should_persist_next_run overwrites any stored value already in the past, so a
    scheduler that restarts each deploy pushes the pointer into the future on every
    boot. On the shared dev DB that left 14 of 34 enabled integrations over a MONTH
    stale (last_sync_status 'success') and invisible to branch 1 alone."""
    where = _where_clause()
    branch = re.search(r'AND \((.*)\)$', where, re.DOTALL)
    assert branch, 'the two staleness branches are not grouped into a single AND (...)'
    body = branch.group(1)
    assert re.search(r'\n\s*OR\s*\n', body), 'the two branches are not OR-ed'
    assert PAST_DUE_ANCHOR in body and SYNC_AGE_ANCHOR in body
    # Both branches sit inside the one parenthesised group, so the status /
    # schedule_enabled / is_running guards above apply to each of them.
    assert body.index(PAST_DUE_ANCHOR) < body.index('OR') < body.index(SYNC_AGE_ANCHOR)


def test_sync_age_branch_is_driven_by_its_own_setting():
    """Two windows tuned separately: the grace window is minutes past a due time,
    the backstop is hours since any sync at all. Sharing one setting would force an
    operator to choose between them."""
    where = _where_clause()
    default = SETTINGS[SYNC_AGE_KEY][0]
    assert re.search(re.escape(SYNC_AGE_ANCHOR) + r"\s*<\s*\n?\s*now\(\)\s*-\s*\(\s*"
                     + _grace_subquery(SYNC_AGE_KEY, default), where), \
        'the sync-age branch does not read DATA_SOURCE_MAX_SYNC_AGE_HOURS'
    assert SETTINGS[SYNC_AGE_KEY][0] == 48, \
        'the backstop default must leave a daily source a full extra period of headroom'


def test_backstop_default_is_not_a_return_to_the_24_hour_window():
    """24 hours equalled the sync period for a daily source, which is what produced
    the original false-positive storm — every source alerting at once. The backstop
    is deliberately looser than the cadence it watches."""
    assert SETTINGS[SYNC_AGE_KEY][0] > 24
    grace_hours = SETTINGS[GRACE_KEY][0] / 60
    assert SETTINGS[SYNC_AGE_KEY][0] > grace_hours, \
        'the backstop must be the looser of the two windows, not the tighter'


# =========================================================================
# subject_sublabel
# =========================================================================

def test_query_projects_the_three_subject_columns():
    """subject_type='integration' rules are consumed by
    AlertsService._evaluate_rules as {id, label, sublabel} subjects."""
    q = _query()
    for col in ('AS subject_id', 'AS subject_label', 'AS subject_sublabel'):
        assert col in q, f"query no longer projects {col}"


def test_subject_sublabel_names_which_branch_matched():
    """With two branches an operator must be able to tell 'the scheduler never
    serviced this job' from 'the data has not refreshed' without re-deriving it from
    the integration record — the two have different causes and different fixes.

    Each arm is a CASE over that branch's own anchor, so a row matched by both
    carries both phrases and a row matched by one carries only that one."""
    expr = _sublabel_expression()
    assert expr.count('CASE WHEN') == 2, 'expected one sublabel arm per branch'
    assert 'overdue for its scheduled run' in expr
    assert 'has not refreshed since' in expr
    # Each arm is gated on the SAME anchor its WHERE branch uses, so a phrase cannot
    # appear for a row that branch did not actually match.
    assert expr.count(PAST_DUE_ANCHOR) == 1
    assert expr.count(SYNC_AGE_ANCHOR) == 1


def test_subject_sublabel_can_never_be_null():
    """Both the UI and the outbound delivery payload read sublabel, and `||`
    propagates NULL — one unguarded nullable operand blanks the whole string.

    concat_ws instead drops NULL arguments and can never itself return NULL, which
    is also what lets an unmatched branch's arm be a bare CASE with no ELSE. Inside
    it, integration_type is NOT NULL and the only other nullable operands are the two
    to_char() calls, each of which must sit in a COALESCE with a literal fallback."""
    expr = _sublabel_expression()
    assert expr.lstrip().startswith('concat_ws('), \
        'sublabel is not built with concat_ws — a || chain blanks on any NULL operand'
    guarded = re.findall(r"COALESCE\('[^']*'\s*\n?\s*\|\| to_char\(i\.\w+", expr)
    assert len(guarded) == expr.count('to_char('), \
        'a to_char() operand in the sublabel is not COALESCE-guarded'
    # ...and each of those COALESCEs falls back to a literal, not another column.
    assert "'overdue for its scheduled run (next run never computed)'" in expr
    assert "'has never refreshed'" in expr


# =========================================================================
# Rule metadata (migration + fresh-install COPY must agree)
# =========================================================================

def test_cadence_is_interval_in_migration_and_schema_full():
    """Under 'pipeline' the check only ran during an asset-generation run, so a
    broken pipeline meant data-source staleness was never checked — and a stale
    source is a common *cause* of a bad run. Both install paths must agree, or a
    fresh install would evaluate the rule somewhere else entirely."""
    assert re.search(r"evaluation_cadence\s*=\s*'interval'", _migration_text())
    assert _copy_row()['evaluation_cadence'] == 'interval'


def test_description_describes_both_branches_and_names_both_settings():
    """The description is the only place the Alerts UI explains the rule. It used to
    assert 24 hours; it must now describe what the query actually does, and name BOTH
    settings — pointing at one of two windows sends an operator to tune the wrong
    knob when the alert is noisy."""
    from tests.unit.modules.alerts.test_auth_audit_seed_rules import _copy_unescape
    description = _copy_unescape(_copy_row()['description'])
    assert '24 hour' not in description and '24h' not in description
    for key in SETTINGS:
        assert key in description, f'description does not point operators at {key}'
    # Both behaviours, not just the schedule-aware half.
    assert 'schedule' in description.lower()
    assert 'refresh' in description.lower()
    # Dual-write: the migration sets the same text the fresh-install COPY carries.
    assert description.replace("'", "''") in _migration_text()


def test_threshold_json_no_longer_advertises_the_dead_24_hour_value():
    """threshold is display-only, but it must not contradict the query — it used to
    advertise {"value": 24, "metric": "hours_since_sync"} that nothing read, and it
    must not now advertise only one of the two windows."""
    threshold = json.loads(_copy_row()['threshold'])
    assert threshold.get('metric') != 'hours_since_sync'
    assert threshold.get('value') != 24
    assert '24' not in json.dumps(threshold)
    # unit stays a non-'percent' unit so AlertsService._current_value reports the
    # raw match count rather than a percentage of a target universe.
    assert threshold['unit'] == 'integrations'
    assert threshold['grace_setting'] == f'{SETTING_CATEGORY}.{GRACE_KEY}'
    assert threshold['max_sync_age_setting'] == f'{SETTING_CATEGORY}.{SYNC_AGE_KEY}'
    # Dual-write: the migration writes the same JSON object.
    m = re.search(r"threshold = '(\{.*?\})'::jsonb", _migration_text(), re.DOTALL)
    assert m, 'migration 140 does not set a threshold'
    assert json.loads(m.group(1)) == threshold


def test_silence_duration_stays_null():
    """One alert covers many integrations, so a per-rule silence would mask a newly
    stale source while an unrelated one sits out its silence window."""
    assert _copy_row()['silence_duration'] == '\\N'
    # No SET silence_duration = ... anywhere in the migration (the header comment
    # names the column, so match the assignment rather than the bare word).
    assert not re.search(r'silence_duration\s*=', _migration_text())


# =========================================================================
# The two window settings
# =========================================================================

def test_settings_upsert_names_value_type_and_default_value():
    """Both columns are NOT NULL with no default, and Postgres validates the
    proposed tuple *before* it detects the conflict — so ON CONFLICT never rescues
    an INSERT that omits them, even when the row already exists."""
    inserts = re.findall(r'INSERT INTO system_settings\s*\(([^)]*)\)', _migration_text())
    assert inserts, 'migration 140 does not seed the window settings'
    for columns_text in inserts:
        columns = [c.strip() for c in columns_text.replace('\n', ' ').split(',')]
        assert 'value_type' in columns
        assert 'default_value' in columns
        assert 'category' in columns and 'key' in columns and 'value' in columns


def test_settings_seed_is_idempotent():
    """DO NOTHING rather than DO UPDATE: re-running the migration must not stomp a
    value an operator has tuned."""
    text = _migration_text()
    assert text.count('ON CONFLICT (category, key) DO NOTHING') == \
        text.count('INSERT INTO system_settings')
    assert 'BEGIN;' in text and 'COMMIT;' in text


@pytest.mark.parametrize('key', sorted(SETTINGS))
def test_settings_row_dual_written_to_schema_full(key):
    """Fresh installs bootstrap from schema-full.sql and stamp migrations as
    already-applied without running them, so a setting seeded only in the migration
    would never exist on a fresh install."""
    default, minimum, maximum, _ = SETTINGS[key]
    row = _settings_copy_row(key)
    assert row is not None, \
        f'{key} missing from the schema-full system_settings COPY block'
    assert row['category'] == SETTING_CATEGORY
    assert row['value'] == str(default)
    assert row['value_type'] == 'integer'
    assert row['default_value'] == str(default)
    assert json.loads(row['validation_rule']) == {'min': minimum, 'max': maximum}


def test_settings_copy_row_ids_match_the_sequence_setval():
    """The COPY block assigns explicit ids; the sequence must be advanced past the
    last of them or the first runtime INSERT collides on the primary key."""
    text = SCHEMA_FULL.read_text()
    m = re.search(r"setval\('public\.system_settings_id_seq',\s*(\d+),\s*true\)", text)
    assert m, 'system_settings_id_seq setval missing from schema-full'
    seeded_ids = [int(_settings_copy_row(key)['id']) for key in SETTINGS]
    assert int(m.group(1)) >= max(seeded_ids)
    # Ids are unique across the COPY block — two rows sharing one would fail the
    # primary key on a fresh install.
    assert len(set(seeded_ids)) == len(seeded_ids)


@pytest.mark.parametrize('key', sorted(SETTINGS))
def test_migration_and_schema_full_seed_the_same_setting_values(key):
    """Drift guard across the two install paths."""
    m = re.search(r"\('alerting',\s*'%s',\s*'(\d+)',\s*'(\w+)',\s*'(\d+)'," % key,
                  _migration_text())
    assert m, f'migration 140 does not seed {key} with literal values'
    value, value_type, default_value = m.groups()
    row = _settings_copy_row(key)
    assert (value, value_type, default_value) == (
        row['value'], row['value_type'], row['default_value'])


@pytest.mark.parametrize('key', sorted(SETTINGS))
def test_settings_schema_definition_matches_the_seeded_row(key):
    """The Settings UI renders from APPLICATION_SETTINGS; the SQL reads the DB row.
    If the two disagree, the admin page validates against bounds the seed never
    honoured (or hides the setting entirely, leaving it untunable — which is the
    exact failure the hardcoded 24 hours was)."""
    default, minimum, maximum, _ = SETTINGS[key]
    definition = APPLICATION_SETTINGS[SETTING_CATEGORY][key]
    assert definition.type == 'integer'
    assert definition.default == default
    assert definition.min == minimum
    assert definition.max == maximum
    # Same section as the ALERT_DELIVERY_* settings they sit beside.
    assert definition.section == 'Alerting'


@pytest.mark.parametrize('key', sorted(SETTINGS))
def test_settings_query_default_matches_the_seeded_default(key):
    """The query's COALESCE fallback is what runs when the row is missing — on an
    install that predates the migration, or after someone deletes the row. If it
    disagrees with the seed, the rule silently changes behaviour depending on
    whether the row happens to exist."""
    default = SETTINGS[key][0]
    assert re.search(_grace_subquery(key, default), _query()), \
        f"the query's fallback for {key} is not {default}"
