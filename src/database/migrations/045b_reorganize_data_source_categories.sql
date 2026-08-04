-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 045_reorganize_data_source_categories.sql
-- Remap integration categories to new taxonomy (#124)
--
-- Old → New:
--   monitoring and analytics → observability platform
--   it service management   → cmdb
--   ot/iot security         → asset discovery
--   network infrastructure  → other  (removed category)
--
-- Idempotent: only updates rows that still have the old value.

BEGIN;

UPDATE integrations
   SET category = 'observability platform',
       updated_at = now()
 WHERE category = 'monitoring and analytics';

UPDATE integrations
   SET category = 'cmdb',
       updated_at = now()
 WHERE category = 'it service management';

UPDATE integrations
   SET category = 'asset discovery',
       updated_at = now()
 WHERE category = 'ot/iot security';

UPDATE integrations
   SET category = 'other',
       updated_at = now()
 WHERE category = 'network infrastructure';

COMMIT;
