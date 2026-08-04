-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Golden-question seed — minimal, idempotent fixture for Phase 11 tests.
--
-- Seeds a small, deterministic asset mix that lets the golden-question
-- harness assert exact counts. Runs inside a single transaction so it is
-- atomic and safe to re-invoke per test session. All rows are scoped by a
-- sentinel owner ('golden-seed@test') so cleanup is trivial.
--
-- Shape of the seed:
--   * 5 Windows Server 2022 — Server / Windows Server 2022
--   * 3 Windows 11 workstations — Workstation / Windows 11
--   * 2 macOS workstations      — Workstation / macOS 14
--   * 2 Linux servers           — Server / Ubuntu 22.04
--   * 1 Android phone           — Mobile / Android 13
-- Total: 13 assets; 7 Servers; 6 Workstations; 1 Mobile; 5 Windows Servers.
--
-- Alerts and integrations are intentionally omitted at this step — they
-- are Phase-11.3 follow-on material; the current question set only exercises
-- asset tooling.

BEGIN;

-- Remove any previous seed data so the counts are exact.
DELETE FROM assets WHERE owner = 'golden-seed@test';

INSERT INTO assets (id, asset, hostname, category, subcategory,
                    os_normalized, os, manufacturer, model, data_source,
                    confidence, owner, location,
                    first_time, last_time, updated_at)
VALUES
  -- 5 Windows Servers
  (gen_random_uuid(), ARRAY['gs-win-01'], ARRAY['gs-win-01'],
   'Server', 'Physical', 'windows server 2022',
   ARRAY['Windows Server 2022'], ARRAY['Dell'], ARRAY['PowerEdge R750'],
   ARRAY['golden-seed'], 0.99, 'golden-seed@test', 'lodz',
   NOW() - INTERVAL '90 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-win-02'], ARRAY['gs-win-02'],
   'Server', 'Physical', 'windows server 2022',
   ARRAY['Windows Server 2022'], ARRAY['Dell'], ARRAY['PowerEdge R750'],
   ARRAY['golden-seed'], 0.99, 'golden-seed@test', 'lodz',
   NOW() - INTERVAL '90 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-win-03'], ARRAY['gs-win-03'],
   'Server', 'Virtual', 'windows server 2022',
   ARRAY['Windows Server 2022'], ARRAY['VMware'], ARRAY['Virtual Machine'],
   ARRAY['golden-seed'], 0.99, 'golden-seed@test', 'warsaw',
   NOW() - INTERVAL '60 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-win-04'], ARRAY['gs-win-04'],
   'Server', 'Virtual', 'windows server 2022',
   ARRAY['Windows Server 2022'], ARRAY['VMware'], ARRAY['Virtual Machine'],
   ARRAY['golden-seed'], 0.99, 'golden-seed@test', 'warsaw',
   NOW() - INTERVAL '60 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-win-05'], ARRAY['gs-win-05'],
   'Server', 'Virtual', 'windows server 2022',
   ARRAY['Windows Server 2022'], ARRAY['VMware'], ARRAY['Virtual Machine'],
   ARRAY['golden-seed'], 0.99, 'golden-seed@test', 'warsaw',
   NOW() - INTERVAL '60 days', NOW(), NOW()),

  -- 3 Windows 11 workstations
  (gen_random_uuid(), ARRAY['gs-ws-01'], ARRAY['gs-ws-01'],
   'Workstation', 'Laptop', 'windows 11 enterprise',
   ARRAY['Windows 11 Enterprise'], ARRAY['Lenovo'], ARRAY['ThinkPad T14'],
   ARRAY['golden-seed'], 0.95, 'golden-seed@test', 'lodz',
   NOW() - INTERVAL '30 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-ws-02'], ARRAY['gs-ws-02'],
   'Workstation', 'Laptop', 'windows 11 enterprise',
   ARRAY['Windows 11 Enterprise'], ARRAY['Lenovo'], ARRAY['ThinkPad T14'],
   ARRAY['golden-seed'], 0.95, 'golden-seed@test', 'warsaw',
   NOW() - INTERVAL '30 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-ws-03'], ARRAY['gs-ws-03'],
   'Workstation', 'Desktop', 'windows 11 enterprise',
   ARRAY['Windows 11 Enterprise'], ARRAY['HP'], ARRAY['EliteDesk 800'],
   ARRAY['golden-seed'], 0.95, 'golden-seed@test', 'warsaw',
   NOW() - INTERVAL '30 days', NOW(), NOW()),

  -- 2 macOS workstations
  (gen_random_uuid(), ARRAY['gs-mac-01'], ARRAY['gs-mac-01'],
   'Workstation', 'Laptop', 'macos 14',
   ARRAY['macOS 14'], ARRAY['Apple'], ARRAY['MacBook Pro 14'],
   ARRAY['golden-seed'], 0.97, 'golden-seed@test', 'lodz',
   NOW() - INTERVAL '10 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-mac-02'], ARRAY['gs-mac-02'],
   'Workstation', 'Laptop', 'macos 14',
   ARRAY['macOS 14'], ARRAY['Apple'], ARRAY['MacBook Pro 14'],
   ARRAY['golden-seed'], 0.97, 'golden-seed@test', 'lodz',
   NOW() - INTERVAL '10 days', NOW(), NOW()),

  -- 2 Linux servers
  (gen_random_uuid(), ARRAY['gs-lin-01'], ARRAY['gs-lin-01'],
   'Server', 'Virtual', 'ubuntu 22.04',
   ARRAY['Ubuntu 22.04'], ARRAY['VMware'], ARRAY['Virtual Machine'],
   ARRAY['golden-seed'], 0.90, 'golden-seed@test', 'warsaw',
   NOW() - INTERVAL '120 days', NOW(), NOW()),
  (gen_random_uuid(), ARRAY['gs-lin-02'], ARRAY['gs-lin-02'],
   'Server', 'Virtual', 'ubuntu 22.04',
   ARRAY['Ubuntu 22.04'], ARRAY['VMware'], ARRAY['Virtual Machine'],
   ARRAY['golden-seed'], 0.90, 'golden-seed@test', 'warsaw',
   NOW() - INTERVAL '120 days', NOW(), NOW()),

  -- 1 Android mobile
  (gen_random_uuid(), ARRAY['gs-and-01'], ARRAY['gs-and-01'],
   'Mobile', 'Phone', 'android 13',
   ARRAY['Android 13'], ARRAY['Samsung'], ARRAY['Galaxy S23'],
   ARRAY['golden-seed'], 0.80, 'golden-seed@test', 'lodz',
   NOW() - INTERVAL '5 days', NOW(), NOW());

COMMIT;
