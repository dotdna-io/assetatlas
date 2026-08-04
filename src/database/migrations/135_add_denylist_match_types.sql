-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/135_add_denylist_match_types.sql
-- Merge-denylist pattern entries (#991). Until now an entry matched exactly one
-- identifier, so knocking out the Proxmox guest-veth MAC range (bc:24:11:*) that
-- over-merges host + LXC + Unifi needed one row per MAC. match_type lets a single
-- entry cover a family:
--
--   mac           -> exact | prefix ('bc:24:11:*') | range (two 48-bit bounds)
--   hostname/fqdn -> exact | prefix ('web*') | suffix ('*.corp.local')
--   serial        -> exact only
--
-- identifier_value stays the canonical, uniqueness-keyed spelling of the entry
-- for every match_type -- the pattern itself, or '<start>-<end>' for a range.
-- range_start/range_end carry the bounds the matcher bisects so it never has to
-- re-parse identifier_value, and let the DB reject a reversed range. Rows that
-- predate this migration are exact by definition and the column default
-- backfills them. Idempotent.
BEGIN;

ALTER TABLE merge_denylist
    ADD COLUMN IF NOT EXISTS match_type text NOT NULL DEFAULT 'exact';
ALTER TABLE merge_denylist ADD COLUMN IF NOT EXISTS range_start bigint;
ALTER TABLE merge_denylist ADD COLUMN IF NOT EXISTS range_end bigint;

ALTER TABLE merge_denylist DROP CONSTRAINT IF EXISTS merge_denylist_match_type_check;
ALTER TABLE merge_denylist ADD CONSTRAINT merge_denylist_match_type_check
    CHECK (match_type IN ('exact', 'prefix', 'suffix', 'range'));

-- Legal (identifier_type, match_type) pairs. Mirrored by MATCH_TYPES_BY_TYPE in
-- src/modules/merge_denylist/encoding.py -- keep the two in step.
ALTER TABLE merge_denylist DROP CONSTRAINT IF EXISTS merge_denylist_type_match_check;
ALTER TABLE merge_denylist ADD CONSTRAINT merge_denylist_type_match_check
    CHECK (
        (identifier_type = 'mac' AND match_type IN ('exact', 'prefix', 'range'))
        OR (identifier_type IN ('hostname', 'fqdn')
            AND match_type IN ('exact', 'prefix', 'suffix'))
        OR (identifier_type = 'serial' AND match_type = 'exact')
    );

-- Bounds exist iff the entry is a range, and a reversed range -- which could
-- never match anything -- is refused at the door.
ALTER TABLE merge_denylist DROP CONSTRAINT IF EXISTS merge_denylist_range_bounds_check;
ALTER TABLE merge_denylist ADD CONSTRAINT merge_denylist_range_bounds_check
    CHECK (
        (match_type = 'range'
         AND range_start IS NOT NULL AND range_end IS NOT NULL
         AND range_start >= 0 AND range_end >= range_start)
        OR (match_type <> 'range' AND range_start IS NULL AND range_end IS NULL)
    );

-- An entry's identity is (identifier_type, identifier_value, match_type). The
-- canonical spellings happen to differ per match_type today ('web01' vs 'web01*'),
-- but uniqueness must not lean on that coincidence -- and create_idempotent()'s
-- ON CONFLICT infers this constraint, so its column list has to match.
ALTER TABLE merge_denylist DROP CONSTRAINT IF EXISTS merge_denylist_unique;
ALTER TABLE merge_denylist ADD CONSTRAINT merge_denylist_unique
    UNIQUE (identifier_type, identifier_value, match_type);

COMMIT;
