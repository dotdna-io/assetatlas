# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration 109: normalize persisted filter operators onto canonical codes.

Encodes the §9/§12 invariants:
  * idempotent (byte-identical re-run),
  * per-condition operator mapped exactly (in->any, is_not_empty->notempty,
    contains->contains, spaced labels -> codes),
  * compliance_criteria.logic ('all'/'any' OR combinator) is preserved
    untouched even though the operator code 'any'/'all' collides literally,
  * discovery gate FAILS loudly on an unmapped spelling,
  * schema-full seed values are byte-identical to what the migration emits.
"""
import json
import os

import psycopg2
import pytest

MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "src", "database", "migrations", "109_normalize_operator_codes.sql",
)


def _apply_migration(cur):
    with open(MIGRATION_PATH) as f:
        sql = f.read()
    # Strip the outer transaction so the test's SAVEPOINT stays intact;
    # the TEMP TABLE uses ON COMMIT DROP, so without an explicit COMMIT it
    # persists for the whole connection — acceptable inside one test, but we
    # DROP it ourselves to allow a second apply in the idempotency test.
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
    cur.execute(sql)
    cur.execute("DROP TABLE IF EXISTS _op_alias;")


def _seed_tag_rule(cur, target_filters, criteria):
    cur.execute(
        """
        INSERT INTO tag_rules (id, name, type, enabled, target_filters, tags, compliance_criteria)
        VALUES (%(id)s, %(name)s, 'compliance', true,
                %(tf)s::jsonb, '{"pass": "p", "fail": "f"}'::jsonb, %(cc)s::jsonb)
        """,
        {
            "id": "tr-109-test",
            "name": "mig109 tag",
            "tf": json.dumps(target_filters),
            "cc": json.dumps(criteria),
        },
    )


@pytest.mark.integration
def test_109_maps_legacy_operators_to_codes(db_transaction):
    cur = db_transaction.cursor()
    _seed_tag_rule(
        cur,
        target_filters=[
            {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "in", "value": "jamf,sccm"},
            {"field_id": {"kind": "static", "name": "OS"}, "operator": "contains", "value": "Win"},
        ],
        criteria={
            "logic": "any",
            "conditions": [
                {"field_id": {"kind": "static", "name": "Domain"}, "operator": "is_not_empty", "value": ""},
                {"field_id": {"kind": "static", "name": "OS"}, "operator": "not equals", "value": "Linux"},
            ],
        },
    )

    _apply_migration(cur)

    cur.execute("SELECT target_filters, compliance_criteria FROM tag_rules WHERE id = 'tr-109-test'")
    tf, cc = cur.fetchone()
    assert [c["operator"] for c in tf] == ["any", "contains"], "target operators not mapped to codes"
    assert [c["operator"] for c in cc["conditions"]] == ["notempty", "neq"], "criteria operators not mapped"
    # logic combinator preserved (collides literally with the 'any' op code).
    assert cc["logic"] == "any", "compliance_criteria.logic must NOT be rewritten"
    # non-operator keys untouched.
    assert tf[0]["value"] == "jamf,sccm"
    assert tf[0]["field_id"] == {"kind": "static", "name": "Data Source"}


@pytest.mark.integration
def test_109_preserves_logic_in_or_fixture(db_transaction):
    """Multi-condition OR rule: operators flip, logic='any' stays 'any'."""
    cur = db_transaction.cursor()
    _seed_tag_rule(
        cur,
        target_filters=[{"field_id": {"kind": "static", "name": "Category"}, "operator": "in", "value": "server,workstation"}],
        criteria={
            "logic": "any",
            "conditions": [
                {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "EDR"},
                {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains", "value": "MDM"},
            ],
        },
    )
    _apply_migration(cur)
    cur.execute("SELECT compliance_criteria FROM tag_rules WHERE id = 'tr-109-test'")
    cc = cur.fetchone()[0]
    assert cc["logic"] == "any"
    assert [c["operator"] for c in cc["conditions"]] == ["contains", "contains"]


@pytest.mark.integration
def test_109_is_idempotent_byte_identical(db_transaction):
    cur = db_transaction.cursor()
    _seed_tag_rule(
        cur,
        target_filters=[{"field_id": {"kind": "static", "name": "Data Source"}, "operator": "in", "value": "jamf"}],
        criteria={"logic": "all", "conditions": [
            {"field_id": {"kind": "static", "name": "OS"}, "operator": "equals", "value": "Win"}]},
    )
    _apply_migration(cur)
    cur.execute("SELECT target_filters::text, compliance_criteria::text FROM tag_rules WHERE id = 'tr-109-test'")
    first = cur.fetchone()
    _apply_migration(cur)  # second run on already-coded data
    cur.execute("SELECT target_filters::text, compliance_criteria::text FROM tag_rules WHERE id = 'tr-109-test'")
    second = cur.fetchone()
    assert first == second, "re-running migration 109 must be byte-identical (codes->codes fixed point)"


@pytest.mark.integration
def test_109_discovery_gate_fails_on_unmapped_operator(db_transaction):
    cur = db_transaction.cursor()
    _seed_tag_rule(
        cur,
        target_filters=[{"field_id": {"kind": "static", "name": "OS"}, "operator": "fizzbuzz", "value": "x"}],
        criteria={"logic": "all", "conditions": []},
    )
    with pytest.raises(psycopg2.errors.RaiseException) as exc:
        _apply_migration(cur)
    assert "fizzbuzz" in str(exc.value), "discovery gate must name the unmapped spelling"
    # the failed DO block aborts the transaction; roll back to the savepoint so
    # the fixture teardown's ROLLBACK TO SAVEPOINT still succeeds.
    cur.execute("ROLLBACK TO SAVEPOINT test_savepoint")
    cur.execute("SAVEPOINT test_savepoint")


@pytest.mark.integration
def test_109_alert_rules_op_key_mapped(db_transaction):
    """Alert filters use key 'op' (not 'operator'); the migration maps that path."""
    cur = db_transaction.cursor()
    cur.execute(
        """
        INSERT INTO alert_rules (id, name, slug, type, severity, enabled, source,
                                 evaluation_type, description, target_filters)
        VALUES (gen_random_uuid(), 'mig109 alert', 'mig109-alert', 'security', 'high',
                true, 'user', 'filter', 'mig109 alert desc',
                '[{"field": "static:OS", "op": "contains", "value": "Win"}]'::jsonb)
        """
    )
    _apply_migration(cur)
    cur.execute("SELECT target_filters FROM alert_rules WHERE slug = 'mig109-alert'")
    tf = cur.fetchone()[0]
    assert tf[0]["op"] == "contains"
    assert tf[0]["field"] == "static:OS", "non-op keys must be preserved"


@pytest.mark.integration
def test_109_fresh_seed_matches_migrated(db_transaction):
    """schema-full.sql posture seed operators are byte-identical to what the
    migration would produce from the pre-109 (legacy 'in') seed."""
    cur = db_transaction.cursor()
    # Current DB rows reflect schema-full (already coded 'any' after dual-write,
    # or 'in' on an old install). Re-coerce to the legacy form, then migrate,
    # and assert the result equals the canonical code 'any'.
    cur.execute(
        """
        UPDATE posture_sub_rules
           SET target_filters = jsonb_set(target_filters, '{0,operator}', '"in"')
         WHERE category_id = 'secured'
        """
    )
    _apply_migration(cur)
    cur.execute(
        "SELECT target_filters->0->>'operator' FROM posture_sub_rules WHERE category_id = 'secured'"
    )
    assert cur.fetchone()[0] == "any", "migrated posture seed operator must be the canonical code"


@pytest.mark.integration
def test_109_tolerates_compliance_criteria_without_conditions(db_transaction):
    """Defense-in-depth: a malformed/legacy compliance_criteria lacking a
    'conditions' key must NOT abort the migration or get wiped to NULL. This is
    unreachable via the API (ComplianceCriteria requires a conditions array) but
    possible from hand-edited JSONB; a one-time prod migration must not hard-fail
    on one bad row."""
    cur = db_transaction.cursor()
    cur.execute(
        """
        INSERT INTO tag_rules (id, name, type, enabled, target_filters, tags, compliance_criteria)
        VALUES ('tr-109-nocond', 'mig109 nocond', 'compliance', true,
                '[{"field_id": {"kind": "static", "name": "OS"}, "operator": "in", "value": "x"}]'::jsonb,
                '{"pass": "p", "fail": "f"}'::jsonb,
                '{"logic": "all"}'::jsonb)
        """
    )
    _apply_migration(cur)  # must not raise
    cur.execute("SELECT target_filters, compliance_criteria FROM tag_rules WHERE id = 'tr-109-nocond'")
    tf, cc = cur.fetchone()
    assert tf[0]["operator"] == "any", "target filter still migrated to the code"
    assert cc == {"logic": "all"}, "conditions-less criteria preserved untouched, not wiped"


@pytest.mark.integration
def test_109_tolerates_non_array_conditions(db_transaction):
    """A hand-corrupted compliance_criteria whose 'conditions' is present but a
    NON-array (e.g. a string) must not abort the migration — the discovery gate,
    _map_conditions, and verification all unwrap via _jarr (array-or-empty), so a
    non-array is left untouched rather than raising 'cannot extract elements from
    a non-array'. Unreachable via the API; defends a one-time prod run."""
    cur = db_transaction.cursor()
    cur.execute(
        """
        INSERT INTO tag_rules (id, name, type, enabled, target_filters, tags, compliance_criteria)
        VALUES ('tr-109-nonarr', 'mig109 nonarr', 'compliance', true,
                '[{"field_id": {"kind": "static", "name": "OS"}, "operator": "in", "value": "x"}]'::jsonb,
                '{"pass": "p", "fail": "f"}'::jsonb,
                '{"logic": "all", "conditions": "garbage"}'::jsonb)
        """
    )
    _apply_migration(cur)  # must not raise
    cur.execute("SELECT target_filters, compliance_criteria FROM tag_rules WHERE id = 'tr-109-nonarr'")
    tf, cc = cur.fetchone()
    assert tf[0]["operator"] == "any", "target filter still migrated to the code"
    assert cc == {"logic": "all", "conditions": "garbage"}, "non-array conditions left untouched"
