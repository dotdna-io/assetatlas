-- Demo data: one manual_upload integration with 10 fake assets.
--
-- Applied at install time only when --with-demo-data is passed. The
-- file_data field is a base64-encoded CSV — the manual_upload connector
-- decodes and parses it on each sync. The field_mapping tells the asset
-- generation pipeline how each CSV column maps to AssetAtlas asset fields.
--
-- The 'department' column has no canonical AssetAtlas field — the field
-- mapper auto-registers it as a data-source-specific (extended) field
-- under field_registry, namespaced as
-- 'demo:_10_sample_assets_department'. This exercises the extended-field
-- path end-to-end (UI rendering, search, etc.).
--
-- Idempotent: re-running is a no-op when an integration with the same
-- name already exists.

INSERT INTO integrations (
    name,
    integration_type,
    category,
    status,
    config,
    field_mapping,
    transformations,
    data_filters,
    description,
    schedule_enabled
) SELECT
    'Demo: 10 sample assets',
    'manual_upload',
    'Data Import',
    'enabled',
    jsonb_build_object(
        'file_format', 'csv',
        'file_name', 'demo_assets.csv',
        'delimiter', ',',
        'encoding', 'utf-8',
        'file_data', 'data:text/csv;base64,aG9zdG5hbWUsaXAsbWFjLG1hbnVmYWN0dXJlcixtb2RlbCxvcyxzZXJpYWxfbnVtYmVyLGRlcGFydG1lbnQsbGFzdF9zZWVuCldTLTAwMS1OWUMsMTAuMTAuMS4xMCwwMDoxQjoyMTozRjpBMTpCMixEZWxsLE9wdGlQbGV4IDcwOTAsV2luZG93cyAxMSBQcm8sREw3MDkwQTEsRmluYW5jZSwyMDI2LTA0LTMwVDA4OjE1OjAwWgpXUy0wMDItTllDLDEwLjEwLjEuMTEsMDA6MUI6MjE6M0Y6QTE6QjMsRGVsbCxMYXRpdHVkZSA3NDIwLFdpbmRvd3MgMTEgUHJvLERMTEFUNzQyMCxGaW5hbmNlLDIwMjYtMDQtMzBUMDk6MjI6MDBaCldTLTAwMy1MT04sMTAuMjAuMi4xNSxBNDo1RTo2MDoxMToyMjozMyxIUCxFbGl0ZUJvb2sgODUwLFdpbmRvd3MgMTEgRW50ZXJwcmlzZSxIUDg1MEVCMSxFbmdpbmVlcmluZywyMDI2LTA0LTI5VDE0OjUwOjAwWgpTUlYtREIwMSwxMC4zMC41LjIwLDAwOjUwOjU2OkFCOkNEOkUxLFZNd2FyZSxWaXJ0dWFsIE1hY2hpbmUsVWJ1bnR1IDI0LjA0IExUUyxWTS1EQjAxLElUIE9wZXJhdGlvbnMsMjAyNi0wNC0zMFQxMTowMDowMFoKU1JWLVdFQjAxLDEwLjMwLjUuMjEsMDA6NTA6NTY6QUI6Q0Q6RTIsVk13YXJlLFZpcnR1YWwgTWFjaGluZSxVYnVudHUgMjIuMDQgTFRTLFZNLVdFQjAxLElUIE9wZXJhdGlvbnMsMjAyNi0wNC0zMFQxMTowNTowMFoKU1JWLVdFQjAyLDEwLjMwLjUuMjIsMDA6NTA6NTY6QUI6Q0Q6RTMsVk13YXJlLFZpcnR1YWwgTWFjaGluZSxVYnVudHUgMjIuMDQgTFRTLFZNLVdFQjAyLElUIE9wZXJhdGlvbnMsMjAyNi0wNC0zMFQxMTowNTowMFoKTUJQLTAxNCwxMC40MC4zLjUwLEYwOjE4Ojk4OkZGOjAwOjE0LEFwcGxlLE1hY0Jvb2sgUHJvIDE2LWluY2ggTTMgUHJvLG1hY09TIDE0LjUgU29ub21hLE1CUE0zUDAxNCxFbmdpbmVlcmluZywyMDI2LTA0LTMwVDEzOjMwOjAwWgpQUklOVC1GTE9PUjIsMTAuNTAuNC41LDNDOjRBOjkyOjExOjg4Ojk5LEhQLExhc2VySmV0IEVudGVycHJpc2UgTTUwNyxIUCBGaXJtd2FyZSA0LjEyLjUsSFBKRVQ1MDdGMixGYWNpbGl0aWVzLDIwMjYtMDQtMjVUMDc6MDA6MDBaClNXSVRDSC1DT1JFLTAxLDEwLjYwLjYuMSwwMDowQzpEQjozMzo0NDo1NSxDaXNjbyxDYXRhbHlzdCA5MzAwLENpc2NvIElPUyBYRSAxNy45LENTQzkzMDBDMSxJVCBPcGVyYXRpb25zLDIwMjYtMDQtMzBUMDA6MDA6MDBaCkFQLUxPQkJZLDEwLjcwLjcuMTAsNjg6N0Y6NzQ6OTk6QUE6QkIsQXJ1YmEsQVAtNTU1LEFydWJhT1MgMTAuNCxBUlU1NTVMQjEsRmFjaWxpdGllcywyMDI2LTA0LTMwVDEyOjAwOjAwWgo='
    ),
    '{
        "hostname": "hostname",
        "ip": "ip",
        "mac": "mac",
        "manufacturer": "manufacturer",
        "model": "model",
        "os_orig": "os",
        "sn": "serial_number",
        "department": "department"
    }'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'Seed data installed via --with-demo-data — 10 assets across workstations, servers, network gear. Includes a data-source-specific "department" field that exercises the extended-field path. Run the asset-generation pipeline to see them flow end-to-end.',
    false
WHERE NOT EXISTS (
    SELECT 1 FROM integrations WHERE name = 'Demo: 10 sample assets'
);
