-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Add IP classification enrichment fields to the field registry as static (core) fields.
-- These are populated by the pipeline's IP classification stage and stored in
-- extended_attributes under the "AssetAtlas" key.

INSERT INTO field_registry (field_name, field_type, db_path, display_name, description, description_source, searchable)
VALUES
    ('ip_private', 'static', 'extended_attributes->''AssetAtlas''->''ip_private''', 'Private IP',
     'ip_private - private IP, internal IP, RFC1918, private address, LAN IP, internal address, non-routable IP. IP addresses in private ranges (10.x, 172.16-31.x, 192.168.x, CGNAT 100.64.x, IPv6 ULA). Classified by the pipeline using Python ipaddress.is_global.',
     'hardcoded', true),

    ('ip_public', 'static', 'extended_attributes->''AssetAtlas''->''ip_public''', 'Public IP',
     'ip_public - public IP, external IP, internet IP, globally routable IP, WAN IP, public address. IP addresses reachable on the public internet. Classified by the pipeline using Python ipaddress.is_global.',
     'hardcoded', true),

    ('ip_type', 'static', 'extended_attributes->''AssetAtlas''->>''ip_type''', 'IP Type',
     'ip_type - IP type, IP classification, address type, network type, private or public. Summary classification of an asset''s IPs: private_only (all internal), public_only (all internet-routable), mixed (both), none (no IPs).',
     'hardcoded', true)

ON CONFLICT (field_name) DO NOTHING;

COMMIT;
