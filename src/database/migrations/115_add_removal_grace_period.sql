-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 115_add_removal_grace_period.sql
-- Asset removal grace period (#795).
-- Adds the missing_since grace anchor and seeds the configurable grace window.
BEGIN;

-- Grace countdown anchor: NULL = present this run; non-NULL = the run at which
-- the asset first went absent from all inputs. Carried-forward (silent) assets
-- keep this set; it is cleared on return.
ALTER TABLE assets ADD COLUMN IF NOT EXISTS missing_since timestamp with time zone;
ALTER TABLE assets_history ADD COLUMN IF NOT EXISTS missing_since timestamp with time zone;

-- Configurable grace window. 0 = disabled (immediate removal, legacy behavior).
INSERT INTO system_settings (category, key, value, value_type, default_value, description)
VALUES (
    'asset_generation', 'removal_grace_period_days', '30', 'integer', '30',
    'Days an asset absent from all inputs is kept (carried forward, silent) before it is hard-removed. 0 = remove immediately (legacy). Effective value is capped by mapping_expiration_days.'
)
ON CONFLICT (category, key) DO NOTHING;

COMMIT;
