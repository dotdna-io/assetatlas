-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 108: add "Windows Events" and "Custom" SIEM/datalake passthrough tiles (#678)
--
-- Two new "Vendor (without direct connection)" wizard tiles: category='Vendor'
-- with direct_connector left NULL (SIEM-only). siem_passthrough_supported and
-- default_transports fall through to the table defaults, matching migration 093.
--
-- Idempotent: INSERT ... ON CONFLICT (id) DO NOTHING.
--
-- Dual-write note: this migration covers existing/upgraded installs. Fresh
-- installs are seeded from schema-full.sql's COPY block before migrations run,
-- and bootstrap_if_empty (deploy/lib/migrate.sh) then marks all migrations as
-- applied WITHOUT running them -- so the same two rows are mirrored in
-- schema-full.sql. See migrations 050 and 091 for the same convention.

BEGIN;

INSERT INTO integration_technologies (id, name, category, logo_path, description) VALUES
    ('windows-events', 'Windows Events',             'Vendor', '/logos/windows.svg',    'Pull Windows Event Log data via SIEM/datalake'),
    ('custom',         'Custom (via SIEM/datalake)', 'Vendor', '/logos/assetatlas.png', 'Ingest custom data via SIEM/datalake')
ON CONFLICT (id) DO NOTHING;

COMMIT;
