-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 093: integration_technologies catalog table + seed 49 vendor tiles
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + INSERT … ON CONFLICT DO NOTHING.

BEGIN;

CREATE TABLE IF NOT EXISTS integration_technologies (
    id                          TEXT PRIMARY KEY,
    name                        TEXT NOT NULL,
    category                    TEXT NOT NULL,
    logo_path                   TEXT NOT NULL,
    direct_connector            TEXT,
    siem_passthrough_supported  BOOLEAN NOT NULL DEFAULT TRUE,
    default_transports          TEXT[] NOT NULL DEFAULT ARRAY['splunk','log_analytics','databricks','sentinelone_siem']::TEXT[],
    description                 TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN integration_technologies.id IS
    'Slug used as integration_type when creating an integration (e.g. ''palo-alto-networks'').';
COMMENT ON COLUMN integration_technologies.direct_connector IS
    'Connector key when a direct integration also exists (NULL for SIEM-only tiles).';
COMMENT ON COLUMN integration_technologies.default_transports IS
    'Transport connector keys offered by default in the wizard.';

INSERT INTO integration_technologies (id, name, category, logo_path, description) VALUES
    ('aruba',                'Aruba',                  'Vendor', '/logos/aruba.png',                'Pull Aruba network data via SIEM/datalake'),
    ('aternity',             'Aternity',               'Vendor', '/logos/aternity.png',             'Pull Aternity DEM data via SIEM/datalake'),
    ('avaya',                'Avaya',                  'Vendor', '/logos/avaya.png',                'Pull Avaya communications data via SIEM/datalake'),
    ('aws',                  'AWS',                    'Vendor', '/logos/aws.png',                  'Pull AWS asset data via SIEM/datalake'),
    ('azure',                'Azure',                  'Vendor', '/logos/azure.png',                'Pull Azure asset data via SIEM/datalake'),
    ('check-point',          'Check Point',            'Vendor', '/logos/checkpoint.png',           'Pull Check Point security data via SIEM/datalake'),
    ('cisco',                'Cisco',                  'Vendor', '/logos/cisco.png',                'Pull Cisco asset data via SIEM/datalake'),
    ('citrix',               'Citrix',                 'Vendor', '/logos/citrix.png',               'Pull Citrix data via SIEM/datalake'),
    ('claroty',              'Claroty',                'Vendor', '/logos/claroty.png',              'Pull Claroty OT/IoT data via SIEM/datalake'),
    ('palo-alto-cortex',     'Palo Alto Cortex',       'Vendor', '/logos/cortex.png',               'Pull Palo Alto Cortex XDR data via SIEM/datalake'),
    ('cybereason',           'Cybereason',             'Vendor', '/logos/cybereason.png',           'Pull Cybereason EDR data via SIEM/datalake'),
    ('cylance',              'Cylance',                'Vendor', '/logos/cylance.png',              'Pull Cylance EDR data via SIEM/datalake'),
    ('datadog',              'Datadog',                'Vendor', '/logos/datadog.png',              'Pull Datadog asset data via SIEM/datalake'),
    ('dynatrace',            'Dynatrace',              'Vendor', '/logos/dynatrace.png',            'Pull Dynatrace data via SIEM/datalake'),
    ('eset',                 'ESET',                   'Vendor', '/logos/eset.png',                 'Pull ESET endpoint data via SIEM/datalake'),
    ('forescout',            'Forescout',              'Vendor', '/logos/forescout.png',            'Pull Forescout NAC data via SIEM/datalake'),
    ('fortinet',             'Fortinet',               'Vendor', '/logos/fortinet.png',             'Pull Fortinet security data via SIEM/datalake'),
    ('google-cloud',         'Google Cloud',           'Vendor', '/logos/google_cloud.svg',         'Pull GCP asset data via SIEM/datalake'),
    ('hp',                   'HP',                     'Vendor', '/logos/hp.png',                   'Pull HP device data via SIEM/datalake'),
    ('huawei',               'Huawei',                 'Vendor', '/logos/huawei.png',               'Pull Huawei device data via SIEM/datalake'),
    ('ibm',                  'IBM',                    'Vendor', '/logos/ibm.png',                  'Pull IBM data via SIEM/datalake'),
    ('ivanti',               'Ivanti',                 'Vendor', '/logos/ivanti.png',               'Pull Ivanti endpoint/UEM data via SIEM/datalake'),
    ('kaspersky',            'Kaspersky',              'Vendor', '/logos/kaspersky.png',            'Pull Kaspersky endpoint data via SIEM/datalake'),
    ('lenovo',               'Lenovo',                 'Vendor', '/logos/lenovo.png',               'Pull Lenovo device data via SIEM/datalake'),
    ('malwarebytes',         'Malwarebytes',           'Vendor', '/logos/malwarebytes.png',         'Pull Malwarebytes endpoint data via SIEM/datalake'),
    ('manageengine',         'ManageEngine',           'Vendor', '/logos/manage_engine.png',        'Pull ManageEngine ITSM data via SIEM/datalake'),
    ('mcafee',               'McAfee',                 'Vendor', '/logos/mcafee.webp',              'Pull McAfee endpoint data via SIEM/datalake'),
    ('new-relic',            'New Relic',              'Vendor', '/logos/new_relic.png',            'Pull New Relic data via SIEM/datalake'),
    ('nozomi-networks',      'Nozomi Networks',        'Vendor', '/logos/nozomi.webp',              'Pull Nozomi OT/IoT data via SIEM/datalake'),
    ('oracle',               'Oracle',                 'Vendor', '/logos/oracle.png',               'Pull Oracle data via SIEM/datalake'),
    ('palo-alto-networks',   'Palo Alto Networks',     'Vendor', '/logos/paloalto.png',             'Pull Palo Alto Networks firewall data via SIEM/datalake'),
    ('prtg',                 'PRTG',                   'Vendor', '/logos/prtg.png',                 'Pull PRTG monitoring data via SIEM/datalake'),
    ('rapid7',               'Rapid7',                 'Vendor', '/logos/rapid7.png',               'Pull Rapid7 vuln/EDR data via SIEM/datalake'),
    ('ricoh',                'Ricoh',                  'Vendor', '/logos/ricoh.png',                'Pull Ricoh device data via SIEM/datalake'),
    ('rockwell-automation',  'Rockwell Automation',    'Vendor', '/logos/rockwell automation.png',  'Pull Rockwell Automation OT data via SIEM/datalake'),
    ('samsung',              'Samsung',                'Vendor', '/logos/samsung.png',              'Pull Samsung device data via SIEM/datalake'),
    ('sccm',                 'Microsoft SCCM / MECM',  'Vendor', '/logos/sccm.png',                 'Pull SCCM/MECM inventory data via SIEM/datalake'),
    ('snow-software',        'Snow Software',          'Vendor', '/logos/snow software.png',        'Pull Snow Software SAM data via SIEM/datalake'),
    ('snowflake',            'Snowflake',              'Vendor', '/logos/snowflake.png',            'Pull Snowflake-hosted asset data via SIEM/datalake'),
    ('sonicwall',            'SonicWall',              'Vendor', '/logos/sonicwall.png',            'Pull SonicWall security data via SIEM/datalake'),
    ('sophos',               'Sophos',                 'Vendor', '/logos/sophos.png',               'Pull Sophos endpoint data via SIEM/datalake'),
    ('symantec',             'Symantec',               'Vendor', '/logos/symantec.png',             'Pull Symantec endpoint data via SIEM/datalake'),
    ('tanium',               'Tanium',                 'Vendor', '/logos/tanium.png',               'Pull Tanium endpoint data via SIEM/datalake'),
    ('tenable',              'Tenable',                'Vendor', '/logos/tenable.png',              'Pull Tenable vuln data via SIEM/datalake'),
    ('trellix',              'Trellix',                'Vendor', '/logos/trellix.png',              'Pull Trellix endpoint data via SIEM/datalake'),
    ('trend-micro',          'Trend Micro',            'Vendor', '/logos/trend_micro.png',          'Pull Trend Micro endpoint data via SIEM/datalake'),
    ('vmware',               'VMware',                 'Vendor', '/logos/vmware.png',               'Pull VMware data via SIEM/datalake'),
    ('zabbix',               'Zabbix',                 'Vendor', '/logos/zabbix.png',               'Pull Zabbix monitoring data via SIEM/datalake'),
    ('zscaler',              'Zscaler',                'Vendor', '/logos/zscaler.svg',              'Pull Zscaler proxy data via SIEM/datalake')
ON CONFLICT (id) DO NOTHING;

COMMIT;
