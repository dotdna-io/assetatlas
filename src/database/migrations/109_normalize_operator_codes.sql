-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 109: Normalize persisted filter operators onto canonical codes.
--
-- Rewrites every operator in the persisted Rules vocabulary onto the canonical
-- short codes used by src/modules/fields/operators.py (Phase 1 resolver).
-- Scope (CRITICAL):
--   * tag_rules.target_filters[].operator, compliance_criteria.conditions[].operator
--   * posture_sub_rules.target_filters[].operator, compliance_criteria.conditions[].operator
--   * alert_rules.target_filters[].op, compliance_criteria.conditions[].op   (alert uses key "op")
-- NEVER touches compliance_criteria.logic — its 'all'/'any' values are the
-- AND/OR combinator and collide literally with the new operator codes.
--
-- The alias map below is the SAME table backing Operator.resolve(): it is a
-- faithful, exhaustive copy of operators._ALIAS_INDEX (code + every alias,
-- lowercased), so it is a strict superset of every spelling resolve() accepts.
-- Codes map to themselves (fixed point) so re-running this migration is a
-- byte-identical no-op. The lookup normalizes the stored value with
-- lower(btrim()) exactly as resolve() does (.lower().strip()). No hand-rolled
-- CASE on display text; a discovery gate fails loudly if any stored spelling is
-- absent from it.
--
-- Depends on: 064 (posture_sub_rules), 078 (alert_rules target_filters /
--             compliance_criteria columns), tag_rules schema.
-- Idempotent: yes (codes->codes fixed point; re-run produces identical JSONB).
-- Forward-only (repo convention; no down-migrations). This rewrites stored rule
-- JSONB in place — recover via the installer's pre-migration DB backup
-- (deploy/update.sh auto-backs up before applying migrations).

BEGIN;

-- ---------------------------------------------------------------------------
-- Alias map: every spelling -> canonical code, generated verbatim from
-- operators._ALIAS_INDEX (84 rows, 17 codes). Kept in lockstep with
-- operators.resolve()'s alias table (Phase 1). Any future operator/alias must
-- be added here (or a later migration) before its rules can be stored.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE _op_alias (spelling TEXT PRIMARY KEY, code TEXT NOT NULL) ON COMMIT DROP;
INSERT INTO _op_alias (spelling, code) VALUES
    -- eq
    ('=', 'eq'), ('==', 'eq'), ('eq', 'eq'), ('equals', 'eq'),
    ('in array', 'eq'), ('in_array', 'eq'), ('is', 'eq'),
    -- neq
    ('!=', 'neq'), ('<>', 'neq'), ('ne', 'neq'), ('neq', 'neq'), ('not', 'neq'),
    ('not equals', 'neq'), ('not in array', 'neq'), ('not_equals', 'neq'), ('not_in_array', 'neq'),
    -- contains
    ('*', 'contains'), ('contains', 'contains'), ('has', 'contains'), ('like', 'contains'), ('~', 'contains'),
    -- ncontains
    ('!~', 'ncontains'), ('ncontains', 'ncontains'), ('not contains', 'ncontains'), ('not_contains', 'ncontains'),
    -- starts
    ('^', 'starts'), ('starts', 'starts'), ('starts with', 'starts'), ('starts_with', 'starts'), ('startswith', 'starts'),
    -- ends
    ('$', 'ends'), ('ends', 'ends'), ('ends with', 'ends'), ('ends_with', 'ends'), ('endswith', 'ends'),
    -- gt
    ('>', 'gt'), ('greater than', 'gt'), ('greater_than', 'gt'), ('gt', 'gt'),
    -- gte
    ('>=', 'gte'), ('greater than or equal', 'gte'), ('greater_than_or_equal', 'gte'), ('gte', 'gte'),
    -- lt
    ('<', 'lt'), ('less than', 'lt'), ('less_than', 'lt'), ('lt', 'lt'),
    -- lte
    ('<=', 'lte'), ('less than or equal', 'lte'), ('less_than_or_equal', 'lte'), ('lte', 'lte'),
    -- any
    ('any', 'any'), ('any of', 'any'), ('any_of', 'any'), ('in', 'any'), ('oneof', 'any'),
    -- all
    ('all', 'all'), ('all of', 'all'), ('all_of', 'all'), ('every', 'all'),
    -- none
    ('none', 'none'), ('none of', 'none'), ('none_of', 'none'), ('without', 'none'),
    -- empty
    ('blank', 'empty'), ('empty', 'empty'), ('is empty', 'empty'), ('is null', 'empty'),
    ('is_empty', 'empty'), ('is_null', 'empty'), ('isempty', 'empty'), ('null', 'empty'),
    -- notempty ('!empty' is notempty's symbol lowercased — added to keep this map
    -- a strict superset of operators._ALIAS_INDEX, like between/within_last below.
    -- Inert on DBs that already applied 109; no stored rule can hold it because it
    -- was previously unresolvable. Enforced by test_migration_109_alias_map_matches_registry.)
    ('!empty', 'notempty'),
    ('exists', 'notempty'), ('is not empty', 'notempty'), ('is not null', 'notempty'),
    ('is_not_empty', 'notempty'), ('is_not_null', 'notempty'), ('isnotempty', 'notempty'),
    ('notempty', 'notempty'), ('notnull', 'notempty'),
    -- is_true
    ('is true', 'is_true'), ('is_true', 'is_true'),
    -- is_false
    ('is false', 'is_false'), ('is_false', 'is_false'),
    -- between / within_last (Phase 3 operators — added 2026-06-04 to keep this map
    -- a strict superset of operators._ALIAS_INDEX. Inert on DBs that already applied
    -- 109, correct for any DB applying it later. Enforced by the drift-guard test
    -- test_cross_surface_operator_parity::test_migration_109_alias_map_matches_registry.
    ('between', 'between'), ('btw', 'between'),
    -- 'within' is within_last's symbol (WITHIN) lowercased — same symbol-coverage
    -- fix as '!empty' above so resolve() accepts the bar-inserted symbol.
    ('within', 'within_last'),
    ('within_last', 'within_last'), ('within last', 'within_last'),
    ('withinlast', 'within_last'), ('in the last', 'within_last');

-- ---------------------------------------------------------------------------
-- Array-or-empty guard: returns x when it is a JSON array, else '[]'. Tolerates
-- a non-array / NULL / missing value (hand-corrupted JSONB) so every array
-- unwrap below never raises 'cannot extract elements from a non-array' and
-- aborts the one-time run. Used by the discovery gate, _map_conditions, and the
-- verification block so all three handle a malformed row identically.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _jarr(x JSONB)
RETURNS JSONB
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN jsonb_typeof(x) = 'array' THEN x ELSE '[]'::jsonb END;
$$;

-- ---------------------------------------------------------------------------
-- Discovery gate: collect every DISTINCT operator across all three tables in
-- both the target_filters[] and compliance_criteria.conditions[] arrays.
-- FAIL the migration if any spelling (normalized like resolve()) is unmapped.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    unmapped TEXT;
BEGIN
    WITH distinct_ops AS (
        -- tag_rules
        SELECT (e->>'operator') AS op FROM tag_rules,
             LATERAL jsonb_array_elements(_jarr(target_filters)) e
        UNION
        SELECT (e->>'operator') FROM tag_rules,
             LATERAL jsonb_array_elements(_jarr(compliance_criteria->'conditions')) e
        UNION
        -- posture_sub_rules
        SELECT (e->>'operator') FROM posture_sub_rules,
             LATERAL jsonb_array_elements(_jarr(target_filters)) e
        UNION
        SELECT (e->>'operator') FROM posture_sub_rules,
             LATERAL jsonb_array_elements(_jarr(compliance_criteria->'conditions')) e
        UNION
        -- alert_rules (key is "op")
        SELECT (e->>'op') FROM alert_rules,
             LATERAL jsonb_array_elements(_jarr(target_filters)) e
        UNION
        SELECT (e->>'op') FROM alert_rules,
             LATERAL jsonb_array_elements(_jarr(compliance_criteria->'conditions')) e
    )
    SELECT string_agg(DISTINCT d.op, ', ')
      INTO unmapped
      FROM distinct_ops d
      LEFT JOIN _op_alias a ON a.spelling = lower(btrim(d.op))
     WHERE d.op IS NOT NULL AND a.code IS NULL;

    IF unmapped IS NOT NULL THEN
        RAISE EXCEPTION
          'Migration 109 discovery gate: unmapped operator spelling(s): %. Extend _op_alias (and operators.resolve) before migrating.',
          unmapped;
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- Helper: map a JSONB array of conditions to codes. opkey is 'operator' or
-- 'op'. Drops nothing; preserves every other key and array order. STABLE (not
-- IMMUTABLE) because it reads the _op_alias table. The lookup normalizes the
-- stored value with lower(btrim()) to mirror operators.resolve(); a non-array
-- arg passes through unchanged via _jarr + the COALESCE fallback.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _map_conditions(arr JSONB, opkey TEXT)
RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        jsonb_agg(
            CASE
                WHEN (e->>opkey) IS NULL THEN e
                ELSE jsonb_set(e, ARRAY[opkey],
                               to_jsonb((SELECT a.code FROM _op_alias a
                                          WHERE a.spelling = lower(btrim(e->>opkey)))))
            END
            ORDER BY ord
        ),
        arr  -- empty/null/non-array passes through unchanged
    )
    FROM jsonb_array_elements(_jarr(arr)) WITH ORDINALITY AS t(e, ord);
$$;

-- ---------------------------------------------------------------------------
-- tag_rules
-- ---------------------------------------------------------------------------
-- The `? 'conditions'` guard keeps a malformed/legacy criteria that lacks a
-- conditions key from collapsing the row: _map_conditions(NULL) -> NULL and
-- jsonb_set(.., NULL) -> NULL would otherwise wipe/abort. Unreachable via the
-- API (ComplianceCriteria requires conditions) but defends a one-time prod run.
UPDATE tag_rules
   SET target_filters = _map_conditions(target_filters, 'operator'),
       compliance_criteria = CASE
           WHEN compliance_criteria IS NULL THEN NULL
           WHEN NOT (compliance_criteria ? 'conditions') THEN compliance_criteria
           ELSE jsonb_set(compliance_criteria, '{conditions}',
                          _map_conditions(compliance_criteria->'conditions', 'operator'))
       END;

-- ---------------------------------------------------------------------------
-- posture_sub_rules
-- ---------------------------------------------------------------------------
UPDATE posture_sub_rules
   SET target_filters = _map_conditions(target_filters, 'operator'),
       compliance_criteria = CASE
           WHEN NOT (compliance_criteria ? 'conditions') THEN compliance_criteria
           ELSE jsonb_set(compliance_criteria, '{conditions}',
                          _map_conditions(compliance_criteria->'conditions', 'operator'))
       END;

-- ---------------------------------------------------------------------------
-- alert_rules (key "op"; filter columns are nullable for sql/external rules)
-- ---------------------------------------------------------------------------
UPDATE alert_rules
   SET target_filters = CASE
           WHEN target_filters IS NULL THEN NULL
           ELSE _map_conditions(target_filters, 'op')
       END,
       compliance_criteria = CASE
           WHEN compliance_criteria IS NULL THEN NULL
           WHEN NOT (compliance_criteria ? 'conditions') THEN compliance_criteria
           ELSE jsonb_set(compliance_criteria, '{conditions}',
                          _map_conditions(compliance_criteria->'conditions', 'op'))
       END;

-- ---------------------------------------------------------------------------
-- Verification: every operator is now a known code AND every logic is still
-- exactly all/any. Fail loudly on any drift.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    bad_op   TEXT;
    bad_logic TEXT;
BEGIN
    WITH all_ops AS (
        SELECT (e->>'operator') AS op FROM tag_rules,
             LATERAL jsonb_array_elements(_jarr(target_filters)) e
        UNION ALL
        SELECT (e->>'operator') FROM tag_rules,
             LATERAL jsonb_array_elements(_jarr(compliance_criteria->'conditions')) e
        UNION ALL
        SELECT (e->>'operator') FROM posture_sub_rules,
             LATERAL jsonb_array_elements(_jarr(target_filters)) e
        UNION ALL
        SELECT (e->>'operator') FROM posture_sub_rules,
             LATERAL jsonb_array_elements(_jarr(compliance_criteria->'conditions')) e
        UNION ALL
        SELECT (e->>'op') FROM alert_rules,
             LATERAL jsonb_array_elements(_jarr(target_filters)) e
        UNION ALL
        SELECT (e->>'op') FROM alert_rules,
             LATERAL jsonb_array_elements(_jarr(compliance_criteria->'conditions')) e
    )
    SELECT string_agg(DISTINCT op, ', ') INTO bad_op
      FROM all_ops
     WHERE op IS NOT NULL
       AND op NOT IN (SELECT code FROM _op_alias);
    IF bad_op IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 109 verification: non-code operator(s) survived: %', bad_op;
    END IF;

    SELECT string_agg(DISTINCT lg, ', ') INTO bad_logic FROM (
        SELECT compliance_criteria->>'logic' AS lg FROM tag_rules WHERE compliance_criteria IS NOT NULL
        UNION ALL
        SELECT compliance_criteria->>'logic' FROM posture_sub_rules
        UNION ALL
        SELECT compliance_criteria->>'logic' FROM alert_rules WHERE compliance_criteria IS NOT NULL
    ) t
    WHERE lg IS NOT NULL AND lg NOT IN ('all', 'any');
    IF bad_logic IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 109 verification: compliance_criteria.logic corrupted to: %', bad_logic;
    END IF;
END$$;

DROP FUNCTION _map_conditions(JSONB, TEXT);
DROP FUNCTION _jarr(JSONB);

COMMIT;
