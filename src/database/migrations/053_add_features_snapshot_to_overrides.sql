-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

ALTER TABLE category_overrides
    ADD COLUMN IF NOT EXISTS features_snapshot JSONB DEFAULT NULL;

COMMENT ON COLUMN category_overrides.features_snapshot IS
    'Asset features at correction time (manufacturer, model, os_normalized, mac_vendor, vendor_category). Used as training signal for ML retraining.';

COMMIT;
