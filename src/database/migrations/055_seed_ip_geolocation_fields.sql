-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Add IP geolocation enrichment fields to the field registry as static (core) fields.
-- These are populated by the pipeline's IP geolocation stage (14c) and stored in
-- extended_attributes under the "AssetAtlas" key.
-- Data source: DB-IP Lite City MMDB (CC-BY-4.0).

INSERT INTO field_registry (field_name, field_type, db_path, display_name, description, description_source, searchable)
VALUES
    ('geo_countries', 'static', 'extended_attributes->''AssetAtlas''->''geo_countries''', 'Geo Country',
     'geo_countries - country, geo country, geolocation country, IP country, location country, country code, ISO country, nation, territory. Countries associated with the asset''s public IP addresses, derived from DB-IP geolocation lookup. Values are country names (e.g. "United States", "Germany"). Multiple countries possible when an asset has public IPs in different regions.',
     'hardcoded', true),

    ('geo_cities', 'static', 'extended_attributes->''AssetAtlas''->''geo_cities''', 'Geo City',
     'geo_cities - city, geo city, geolocation city, IP city, location city, metro area, municipality, town. Cities associated with the asset''s public IP addresses, derived from DB-IP geolocation lookup. Accuracy is typically metro-area level (~50 km). Multiple cities possible when an asset has public IPs in different locations.',
     'hardcoded', true),

    ('geo_regions', 'static', 'extended_attributes->''AssetAtlas''->''geo_regions''', 'Geo Region',
     'geo_regions - region, geo region, state, province, subdivision, geolocation region, IP region, location region, territory. Regions or states associated with the asset''s public IP addresses, derived from DB-IP geolocation lookup. Uses first-level administrative subdivisions (e.g. "California", "Bavaria", "New South Wales").',
     'hardcoded', true)

ON CONFLICT (field_name) DO NOTHING;

COMMIT;
