-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 092: convert owner/location/user_field/ipam_location to TEXT[]
--
-- Issue #495. These four base asset fields represent per-source observations,
-- so multi-source disagreement should be preserved instead of silently collapsed
-- to one value. After this migration every per-source-observable base field on
-- `assets` is uniformly TEXT[] (asset, hostname, fqdn, ip, mac, sn,
-- mac_vendor_enriched, manufacturer, model, data_source, data_source_category,
-- vendor_category, os, owner, user_field, ipam_location, location).
--
-- `os_normalized` and `os_orig` intentionally stay scalar — they hold the
-- pipeline-selected best value, not per-source observations. The existing `os`
-- TEXT[] already carries the full per-source raw OS list.
--
-- Backfill: existing single values wrap into a 1-element array; empty strings
-- and NULL become NULL.
--
-- Idempotent. Safe to re-run: the per-column ALTER is guarded by an
-- information_schema check so applying the migration to an already-converted
-- DB is a no-op.
--
-- Ops note: each ALTER … TYPE rewrites the table. `assets_history` is a
-- TimescaleDB hypertable — on a large fleet this rewrites every chunk and
-- can take 10+ minutes. Run during a low-traffic window and watch
-- replication lag if WAL streaming is in use.

BEGIN;

DO $$
DECLARE
    tbl TEXT;
    col TEXT;
    current_type TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['assets', 'assets_history'] LOOP
        FOREACH col IN ARRAY ARRAY['owner', 'location', 'user_field', 'ipam_location'] LOOP
            SELECT data_type INTO current_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = tbl
              AND column_name = col;

            IF current_type IS NULL THEN
                RAISE NOTICE 'Skipping %.%: column not found', tbl, col;
                CONTINUE;
            END IF;

            IF current_type = 'ARRAY' THEN
                RAISE NOTICE 'Skipping %.%: already array', tbl, col;
                CONTINUE;
            END IF;

            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN %I TYPE TEXT[] USING '
                'CASE WHEN %I IS NULL OR %I = '''' THEN NULL ELSE ARRAY[%I] END',
                tbl, col, col, col, col
            );
            RAISE NOTICE 'Converted %.% to TEXT[]', tbl, col;
        END LOOP;
    END LOOP;
END $$;

-- Verification: print final column types so the migration log shows the result.
SELECT table_name, column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('assets', 'assets_history')
  AND column_name IN ('owner', 'location', 'user_field', 'ipam_location')
ORDER BY table_name, column_name;

COMMIT;
