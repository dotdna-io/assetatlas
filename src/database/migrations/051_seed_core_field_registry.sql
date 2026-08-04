-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Seed the 20 core AssetAtlas fields into field_registry.
-- These are the static fields that exist in every deployment.
-- Idempotent: ON CONFLICT skips existing rows.

INSERT INTO field_registry (field_name, field_type, db_path, display_name, description, description_source, searchable)
VALUES
    ('hostname', 'static', 'hostname', 'Hostname',
     'hostname - computer name, machine name, device name, server name, workstation name, PC name, node name, system name, NetBIOS name, DNS short name, DNS name. The unique name identifying a computer on a network (without domain suffix). Examples: SERVER01, LAPTOP-JOHN',
     'hardcoded', true),

    ('ip', 'static', 'ip', 'IP Address',
     'ip - IP address, network address, IPv4, IPv6, internet protocol, host IP, device IP, layer 3 address, network layer address. The logical network address. Examples: 192.168.1.100, 10.0.0.5',
     'hardcoded', true),

    ('mac', 'static', 'mac', 'MAC Address',
     'mac - MAC address, hardware address, physical address, ethernet address, NIC address, layer 2 address, data link address, burnt-in address, network interface card address. The hardware identifier (AA:BB:CC:DD:EE:FF format).',
     'hardcoded', true),

    ('sn', 'static', 'sn', 'Serial Number',
     'sn - serial number, serial, service tag, asset tag, hardware ID, chassis serial, product serial, S/N, id, identifier, unique number. The manufacturer tracking number on the device sticker.',
     'hardcoded', true),

    ('fqdn', 'static', 'fqdn', 'FQDN',
     'fqdn - fully qualified domain name, full hostname, full DNS name, complete hostname, full domain name. The complete name with domain: server01.corp.company.com',
     'hardcoded', true),

    ('category', 'static', 'category', 'Category',
     'category - device type, asset type, classification, type, device class, asset class, hardware type. The high-level grouping: Server, Workstation, Laptop, Desktop, VM, Network Device, IoT, Printer, Mobile Device.',
     'hardcoded', true),

    ('subcategory', 'static', 'subcategory', 'Subcategory',
     'subcategory - subtype, detailed type, specific classification, secondary category, device subtype. Granular classification like Web Server or Gaming Laptop within the main category.',
     'hardcoded', true),

    ('os_normalized', 'static', 'os_normalized', 'Operating System',
     'os_normalized - operating system, OS, platform, system software, Windows, Linux, macOS, Ubuntu, Red Hat, OS version, OS type, system type. What software runs the computer.',
     'hardcoded', true),

    ('manufacturer', 'static', 'manufacturer', 'Manufacturer',
     'manufacturer - vendor, make, brand, OEM, hardware vendor, producer, company name. Who made the hardware: Dell, HP, Cisco, Apple, Lenovo, VMware.',
     'hardcoded', true),

    ('model', 'static', 'model', 'Model',
     'model - model number, product name, hardware model, device model, SKU, part number, form factor, chassis type. The specific product: PowerEdge R740, MacBook Pro, ThinkPad X1.',
     'hardcoded', true),

    ('location', 'static', 'location', 'Location',
     'location - site, building, floor, room, rack, data center, datacenter, DC, rack location, physical location, where, office, branch, campus, facility. Where the device is physically placed.',
     'hardcoded', true),

    ('owner', 'static', 'owner', 'Owner',
     'owner - assigned to, user, responsible, contact, department, team, custodian, manager, assignee, accountable, cost center, who owns, ownership. Who is accountable for this asset.',
     'hardcoded', true),

    ('data_source', 'static', 'data_source', 'Data Source',
     'data_source - source, integration, provider, discovered by, origin, imported from, source system, discovery source. Which system discovered this asset.',
     'hardcoded', true),

    ('data_source_category', 'static', 'data_source_category', 'Data Source Category',
     'data_source_category - source type, source category, integration type, discovery method. Category of the data source: EDR, CMDB, Directory Service, Network Scanner, Cloud Platform.',
     'hardcoded', true),

    ('first_seen', 'static', 'first_seen', 'First Seen',
     'first_seen - discovered, created, added, first discovered, initial discovery, when added, discovery date, first detected, first found. When the asset was first found by AssetAtlas.',
     'hardcoded', true),

    ('last_seen', 'static', 'last_seen', 'Last Seen',
     'last_seen - last active, last updated, recent, latest, last observation, last check-in, most recent, last activity. When the asset was most recently seen.',
     'hardcoded', true),

    ('confidence', 'static', 'confidence', 'Confidence',
     'confidence - confidence score, score, quality, accuracy, certainty, reliability, data quality, trust score. How reliable the asset data is (0-100%).',
     'hardcoded', true),

    ('os_eol_status', 'static', 'os_eol_status', 'EOL Status',
     'os_eol_status - EOL status, end of life, end of support, supported, unsupported, lifecycle, support status, retired, decommissioned, patch status. Whether the OS is still supported.',
     'hardcoded', true),

    ('ipam_location', 'static', 'ipam_location', 'IPAM Location',
     'ipam_location - IPAM, IP management, subnet, network location, network segment, IP location, subnet location. Location information from IP Address Management system based on network subnet assignments.',
     'hardcoded', true),

    ('mac_vendor_enriched', 'static', 'mac_vendor_enriched', 'MAC Vendor',
     'mac_vendor_enriched - MAC vendor, NIC vendor, OUI, OUI vendor, OUI lookup, network card vendor, network card manufacturer, ethernet vendor. The vendor identified from MAC address OUI prefix lookup.',
     'hardcoded', true)

ON CONFLICT (field_name) DO NOTHING;

COMMIT;
