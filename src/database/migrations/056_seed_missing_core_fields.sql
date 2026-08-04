-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Add core fields that were missing from the field registry.
-- os_eol_date and os_is_lts are populated by the OS EOL enrichment stage.
-- user_field is a free-text field editable by users.

INSERT INTO field_registry (field_name, field_type, db_path, display_name, description, description_source, searchable)
VALUES
    ('os_eol_date', 'static', 'os_eol_date', 'OS EOL Date',
     'os_eol_date - EOL date, end of life date, end of support date, OS EOL date, support end date, retirement date, sunset date. The date when the asset''s operating system reaches end of life and stops receiving security updates. Derived from OS normalization and EOL reference data during the pipeline.',
     'hardcoded', true),

    ('os_is_lts', 'static', 'os_is_lts', 'OS Is LTS',
     'os_is_lts - LTS, long term support, is LTS, OS LTS, LTSC, long term servicing, stable release, extended support. Whether the asset''s operating system is a Long Term Support release. LTS releases receive extended security updates and have longer support lifecycles. Derived from OS normalization during the pipeline.',
     'hardcoded', true),

    ('user_field', 'static', 'user_field', 'User Field',
     'user_field - user field, custom field, user defined, user data, custom attribute, user note, user label. A free-text field that can be set per asset by users for custom categorization, notes, or metadata not captured by other fields.',
     'hardcoded', true)

ON CONFLICT (field_name) DO NOTHING;

COMMIT;
