// SPDX-License-Identifier: AGPL-3.0-or-later
// Data source logo mappings — ported from legacy public/js/shared/data-source-logos.js.
// Provides brand colors and logo paths for integration source badges.

import { getConnectorDisplayName } from '@/pages/integrations/integration-utils';

export interface DataSourceLogo {
  logo: string;  // filename in /logos/
  color: string;  // hex brand color
}

export const DATA_SOURCE_LOGOS: Record<string, DataSourceLogo> = {
  // Core
  'assetatlas': { logo: '/assetatlas_logo.png', color: '#64748b' },

  // EDR / Endpoint Security
  'crowdstrike': { logo: 'crowdstrike.png', color: '#E11029' },
  'crowdstrike falcon': { logo: 'crowdstrike.png', color: '#E11029' },
  'defender': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
  'microsoft defender': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
  'microsoft defender api': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
  'microsoft defender telemetry': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
  'defender for endpoint': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
  'microsoft defender for endpoint': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
  'sentinel one': { logo: 'sentinel one.webp', color: '#6C2EB9' },
  'sentinelone': { logo: 'sentinel one.webp', color: '#6C2EB9' },
  'sentinelone ranger': { logo: 'sentinel one.webp', color: '#6C2EB9' },
  'cortex': { logo: 'cortex.png', color: '#F04E23' },
  'cylance': { logo: 'cylance.png', color: '#000000' },
  'cybereason': { logo: 'cybereason.png', color: '#6B4FBB' },
  'malwarebytes': { logo: 'malwarebytes.png', color: '#0052CC' },
  'sophos': { logo: 'sophos.png', color: '#2A5CAA' },
  'mcafee': { logo: 'mcafee.webp', color: '#C8102E' },
  'symantec': { logo: 'symantec.png', color: '#FDB913' },
  'kaspersky': { logo: 'kaspersky.png', color: '#006D5C' },
  'eset': { logo: 'eset.png', color: '#00A4E4' },
  'trend micro': { logo: 'trend_micro.png', color: '#D71920' },
  'trellix': { logo: 'trellix.png', color: '#FF6B00' },
  'carbon black': { logo: 'vmware.png', color: '#717074' },

  // SIEM / Log Management
  'splunk': { logo: 'splunk.png', color: '#65A637' },
  'microsoft sentinel': { logo: 'microsoft sentinel.png', color: '#0078D4' },
  'sentinel': { logo: 'microsoft sentinel.png', color: '#0078D4' },
  'log analytics': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
  'log analytics workspace': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
  'log analytics workspaces': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
  'azure log analytics': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
  'microsoft log analytics': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
  'microsoft log analytics agent': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
  'log analytics agent': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
  'sumo logic': { logo: 'sumo_logic.png', color: '#000099' },
  'datadog': { logo: 'datadog.png', color: '#632CA6' },
  'dynatrace': { logo: 'dynatrace.png', color: '#1496FF' },
  'new relic': { logo: 'new_relic.png', color: '#008C99' },

  // Endpoint Management
  'intune': { logo: 'intune.png', color: '#0078D4' },
  'intune full': { logo: 'intune.png', color: '#0078D4' },
  'microsoft intune': { logo: 'intune.png', color: '#0078D4' },
  'sccm': { logo: 'sccm.png', color: '#0078D4' },
  'lansweeper': { logo: 'lansweeper.png', color: '#F37021' },
  'tanium': { logo: 'tanium.png', color: '#C8102E' },
  'ivanti': { logo: 'ivanti.png', color: '#E31837' },
  'snow software': { logo: 'snow software.png', color: '#00A1E0' },
  'manage engine': { logo: 'manage_engine.png', color: '#D32F2F' },
  'manageengine': { logo: 'manage_engine.png', color: '#D32F2F' },
  'airwatch': { logo: 'airwatch.png', color: '#F37021' },
  'jamf': { logo: 'apple.png', color: '#000000' },

  // ITSM / CMDB
  'servicenow': { logo: 'servicenow.png', color: '#81B5A1' },
  'servicenow cmdb': { logo: 'servicenow.png', color: '#81B5A1' },

  // Vulnerability Management
  'qualys': { logo: 'qualys.png', color: '#ED1C24' },
  'tenable': { logo: 'tenable.png', color: '#0A1F44' },
  'nessus': { logo: 'tenable.png', color: '#0A1F44' },
  'nessus scanner': { logo: 'tenable.png', color: '#0A1F44' },
  'rapid7': { logo: 'rapid7.png', color: '#E85D04' },

  // Network Security
  'forescout': { logo: 'forescout.png', color: '#00A3E0' },
  'armis': { logo: 'armis.png', color: '#6C5CE7' },
  'zscaler': { logo: 'zscaler.svg', color: '#049FD9' },
  'palo alto': { logo: 'paloalto.png', color: '#F04E23' },
  'paloalto': { logo: 'paloalto.png', color: '#F04E23' },
  'fortinet': { logo: 'fortinet.png', color: '#DA291C' },
  'cisco': { logo: 'cisco.png', color: '#049FD9' },
  'checkpoint': { logo: 'checkpoint.png', color: '#E21836' },
  'juniper': { logo: 'juniper.png', color: '#00A0E3' },
  'juniper mist': { logo: 'juniper.png', color: '#00A0E3' },
  'aruba': { logo: 'aruba.png', color: '#F37021' },

  // Cloud Platforms
  'azure': { logo: 'azure.png', color: '#0078D4' },
  'azure resource graph': { logo: 'Azure-Resource-Graph.svg', color: '#0078D4' },
  'azure container instances': { logo: 'Azure-Container-Instances.svg', color: '#0078D4' },
  'azure data explorer': { logo: 'Azure-Data-Explorer.svg', color: '#0078D4' },
  'azure data lake': { logo: 'Azure-Data-Lake-Store.svg', color: '#0078D4' },
  'azure data lake store': { logo: 'Azure-Data-Lake-Store.svg', color: '#0078D4' },
  'azure event hubs': { logo: 'Azure-Event-Hubs.svg', color: '#0078D4' },
  'azure virtual machines': { logo: 'Azure-Virtual-Machines.svg', color: '#0078D4' },
  'azure vm': { logo: 'Azure-Virtual-Machines.svg', color: '#0078D4' },
  'aws': { logo: 'aws.png', color: '#FF9900' },
  'google cloud': { logo: 'google_cloud.svg', color: '#4285F4' },
  'gcp': { logo: 'google_cloud.svg', color: '#4285F4' },
  'databricks': { logo: 'Databricks.svg', color: '#FF3621' },

  // OT / ICS Security
  'claroty': { logo: 'claroty.png', color: '#00B4D8' },
  'nozomi': { logo: 'nozomi.webp', color: '#00A3A1' },
  'rockwell': { logo: 'rockwell automation.png', color: '#C8102E' },
  'rockwell automation': { logo: 'rockwell automation.png', color: '#C8102E' },
  'schneider': { logo: 'schneider.png', color: '#3DCD58' },

  // Identity / Directory
  'ldap': { logo: 'MS-active-directory.png', color: '#0078D4' },
  'ldap computers': { logo: 'MS-active-directory.png', color: '#0078D4' },
  'active directory': { logo: 'MS-active-directory.png', color: '#0078D4' },
  'active directory authentications': { logo: 'MS-active-directory.png', color: '#0078D4' },
  'ad': { logo: 'MS-active-directory.png', color: '#0078D4' },
  'microsoft active directory': { logo: 'MS-active-directory.png', color: '#0078D4' },
  'azure ad': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
  'azure active directory': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
  'entra': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
  'entra id': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
  'entra devices': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
  'microsoft entra': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
  'microsoft entra id': { logo: 'microsoft-entra-id.png', color: '#0078D4' },

  // Monitoring
  'zabbix': { logo: 'zabbix.png', color: '#D40000' },
  'prtg': { logo: 'prtg.png', color: '#00B4CC' },
  'solarwinds': { logo: 'solarwinds.png', color: '#F37021' },

  // Virtualization
  'vmware': { logo: 'vmware.png', color: '#717074' },
  'citrix': { logo: 'citrix.png', color: '#452170' },

  // Hardware Vendors
  'dell': { logo: 'dell.png', color: '#007DB8' },
  'hp': { logo: 'hp.png', color: '#0096D6' },
  'lenovo': { logo: 'lenovo.png', color: '#E2231A' },
  'apple': { logo: 'apple.png', color: '#000000' },
  'samsung': { logo: 'samsung.png', color: '#1428A0' },
  'huawei': { logo: 'huawei.png', color: '#E31837' },
  'ricoh': { logo: 'ricoh.png', color: '#C8102E' },
  'avaya': { logo: 'avaya.png', color: '#DA291C' },
  'ibm': { logo: 'ibm.png', color: '#0530AD' },
  'oracle': { logo: 'oracle.png', color: '#C74634' },
  'sap': { logo: 'sap.png', color: '#0FAAFF' },

  // Backup
  'commvault': { logo: 'commvault.png', color: '#F37021' },

  // Other
  'aternity': { logo: 'aternity.png', color: '#00A4E4' },
  'snowflake': { logo: 'snowflake.png', color: '#29B5E8' },
};

export const DEFAULT_BRAND_COLOR = '#64748b';

/**
 * Case-insensitive lookup of data source logo info.
 * Normalizes underscores to spaces for matching (e.g., "manage_engine" -> "manage engine").
 */
export function getDataSourceLogo(name: string): DataSourceLogo | null {
  if (!name) return null;
  const normalized = name.toLowerCase().trim();

  // Direct match
  const direct = DATA_SOURCE_LOGOS[normalized];
  if (direct) return direct;

  // Try replacing underscores with spaces
  const withSpaces = normalized.replace(/_/g, ' ');
  if (withSpaces !== normalized) {
    const spaced = DATA_SOURCE_LOGOS[withSpaces];
    if (spaced) return spaced;
  }

  return null;
}

/**
 * Convert hex color to rgba string.
 */
export function hexToRgba(hex: string, opacity: number): string {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return `rgba(100, 116, 139, ${opacity})`;
  const r = parseInt(result[1], 16);
  const g = parseInt(result[2], 16);
  const b = parseInt(result[3], 16);
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

// Reverse index: logo filename (lowercased basename) -> brand color. Lets us
// recover a brand colour when we only have a logo *path* (e.g. from the backend
// logo-map) rather than a source name.
const LOGO_FILE_COLORS: Record<string, string> = (() => {
  const m: Record<string, string> = {};
  for (const v of Object.values(DATA_SOURCE_LOGOS)) {
    m[(v.logo.split('/').pop() ?? v.logo).toLowerCase()] = v.color;
  }
  return m;
})();

/** Brand color for a logo path/filename, or null if the file isn't known. */
export function colorForLogoPath(path: string): string | null {
  if (!path) return null;
  const base = (path.split('/').pop() ?? path).toLowerCase();
  return LOGO_FILE_COLORS[base] ?? null;
}

/**
 * Resolve the chip logo for a data-source *name*, in priority order:
 *   1. the integration's own logo from the backend logo-map — handles renamed
 *      and custom-uploaded sources whose name matches no brand (e.g. "Acme Intune");
 *   2. the static brand map keyed by the name itself;
 *   3. the integration_type of the group the name belongs to.
 * Returns null when nothing matches (caller renders the initial-letter chip).
 *
 * `logoByName` (name -> logo path) and `typeByName` (name -> integration_type)
 * are keyed by lowercased, trimmed name.
 */
export function resolveDataSourceLogo(
  name: string,
  logoByName: Map<string, string>,
  typeByName: Map<string, string>,
): DataSourceLogo | null {
  const key = (name ?? '').toLowerCase().trim();

  const apiLogo = logoByName.get(key);
  if (apiLogo) {
    return { logo: apiLogo, color: colorForLogoPath(apiLogo) ?? DEFAULT_BRAND_COLOR };
  }

  const direct = getDataSourceLogo(name);
  if (direct) return direct;

  const it = typeByName.get(key);
  if (it) {
    return getDataSourceLogo(it) ?? getDataSourceLogo(getConnectorDisplayName(it));
  }

  return null;
}
