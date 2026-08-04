// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Format an ISO timestamp as relative time ("2h ago", "3d ago").
 * Returns "Never" if input is null/undefined.
 */
export function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return 'Never';
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = now - then;
  if (diffMs < 0) return 'just now';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

/**
 * Format a future ISO timestamp ("in 3d", "in 2h").
 * Returns null if input is null/undefined.
 */
export function formatFutureTime(isoString: string | null | undefined): string | null {
  if (!isoString) return null;
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = then - now;
  if (diffMs < 0) return 'Overdue';
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return 'Starting soon';
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(diffMs / 3600000);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.floor(diffMs / 86400000);
  if (days < 7) return `in ${days}d`;
  return new Date(isoString).toLocaleDateString();
}

/**
 * Human-readable description of a cron expression.
 * Parses the 5-field cron into natural language.
 */
export function getScheduleDescription(cron: string | null | undefined): string {
  if (!cron) return 'No schedule';
  const shorthand: Record<string, string> = {
    '@hourly': '0 * * * *',
    '@daily': '0 0 * * *',
    '@weekly': '0 0 * * 0',
    '@monthly': '0 0 1 * *',
    '@yearly': '0 0 1 1 *',
    '@annually': '0 0 1 1 *',
  };
  const normalized = shorthand[cron] || cron;
  const parts = normalized.trim().split(/\s+/);
  if (parts.length !== 5) return `Custom (${cron})`;

  const [min, hour, dom, mon, dow] = parts;
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  const stepValue = (field: string): number | null => {
    const m = field.match(/^\*\/(\d+)$/);
    return m ? Number(m[1]) : null;
  };
  const isNumeric = (field: string): boolean => /^\d+$/.test(field);
  const restAllStar = dom === '*' && mon === '*' && dow === '*';

  // Every N minutes (e.g., */15 * * * *)
  const minStep = stepValue(min);
  if (minStep !== null && hour === '*' && restAllStar) {
    return minStep === 1 ? 'Every minute' : `Every ${minStep} minutes`;
  }

  // Every N hours (e.g., 0 */4 * * *, 30 */2 * * *)
  const hourStep = stepValue(hour);
  if (hourStep !== null && isNumeric(min) && restAllStar) {
    const offset = Number(min) === 0 ? '' : ` at :${min.padStart(2, '0')}`;
    return hourStep === 1 ? `Hourly${offset}` : `Every ${hourStep} hours${offset}`;
  }

  // Hourly variations (hour = '*')
  if (hour === '*' && restAllStar) {
    if (min === '*') return 'Every minute';
    if (isNumeric(min)) return `Hourly at :${min.padStart(2, '0')}`;
    return `Custom (${cron})`;
  }

  // Below patterns require numeric minute and hour to render a HH:MM time
  if (!isNumeric(min) || !isNumeric(hour)) return `Custom (${cron})`;
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;

  // Common day-of-week shortcuts
  if (dom === '*' && mon === '*' && dow === '1-5') return `Weekdays at ${time}`;
  if (dom === '*' && mon === '*' && (dow === '0,6' || dow === '6,0')) return `Weekends at ${time}`;

  // Daily
  if (restAllStar) return `Daily at ${time}`;

  // Weekly (single day-of-week)
  if (dom === '*' && mon === '*' && isNumeric(dow)) {
    return `Weekly ${days[Number(dow)] || dow} at ${time}`;
  }

  // Monthly (single day-of-month)
  if (mon === '*' && dow === '*' && isNumeric(dom)) {
    return `Monthly on day ${dom} at ${time}`;
  }

  return `Custom (${cron})`;
}

/**
 * Map integration_type to display name.
 */
const CONNECTOR_NAMES: Record<string, string> = {
  azure_ad: 'Microsoft Entra ID',
  active_directory: 'Active Directory LDAP',
  crowdstrike: 'CrowdStrike Falcon',
  intune: 'Microsoft Intune',
  jamf: 'Jamf Pro',
  lansweeper: 'Lansweeper',
  log_analytics: 'Azure Log Analytics',
  manual_upload: 'Manual File Upload',
  qualys_assets: 'Qualys',
  sentinelone: 'SentinelOne',
  sentinelone_siem: 'SentinelOne SIEM',
  servicenow_cmdb: 'ServiceNow CMDB',
  solarwinds_orion: 'SolarWinds Orion',
  splunk: 'Splunk Enterprise',
};

export function getConnectorDisplayName(type: string): string {
  return CONNECTOR_NAMES[type] || type;
}

/**
 * Format a duration in seconds to human-readable (e.g., "1m 23s").
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

/**
 * Get age category for last-run coloring.
 * <7 days = 'recent', 7-30 = 'stale', >30 = 'old'
 */
export function getRunAge(isoString: string | null | undefined): 'recent' | 'stale' | 'old' | 'never' {
  if (!isoString) return 'never';
  const days = (Date.now() - new Date(isoString).getTime()) / 86400000;
  if (days < 7) return 'recent';
  if (days < 30) return 'stale';
  return 'old';
}

/**
 * List of fields that should never be shown (sensitive).
 */
const SENSITIVE_FIELDS = new Set([
  'password', 'token', 'api_key', 'api_token', 'secret',
  'client_secret', 'credentials', 'file_data', 'private_key',
]);

export function isSensitiveField(key: string): boolean {
  return SENSITIVE_FIELDS.has(key.toLowerCase());
}

/**
 * Standard framework target fields for field mapping.
 */
export const FRAMEWORK_FIELDS = [
  { key: 'hostname', label: 'Hostname', description: 'Device hostname. At least one of: hostname, fqdn, ip, or mac is required.' },
  { key: 'fqdn', label: 'FQDN', description: 'Fully qualified domain name. At least one of: hostname, fqdn, ip, or mac is required.' },
  { key: 'ip', label: 'IP Address', description: 'IPv4 or IPv6 address. At least one of: hostname, fqdn, ip, or mac is required.' },
  { key: 'mac', label: 'MAC Address', description: 'Hardware address. At least one of: hostname, fqdn, ip, or mac is required.' },
  { key: 'sn', label: 'Serial Number', description: 'Manufacturer serial number. Stored in sn column.' },
  { key: 'os', label: 'Operating System', description: 'OS name and version.' },
  { key: 'manufacturer', label: 'Manufacturer', description: 'Hardware manufacturer.' },
  { key: 'model', label: 'Model', description: 'Hardware model name.' },
  { key: 'vendor_category', label: 'Category', description: 'Device type/category.' },
  { key: 'owner', label: 'Owner', description: 'Asset owner or primary user.' },
  { key: 'location', label: 'Location', description: 'Physical or logical location.' },
  { key: 'user', label: 'User', description: 'Last logged-in or assigned user.' },
  { key: 'first_time', label: 'First Seen', description: 'First time the asset was observed.' },
  { key: 'last_time', label: 'Last Seen', description: 'Last time the asset was observed.' },
] as const;

/**
 * Access a value via dot-notation path (e.g., "computer.general.name").
 *
 * Prefers a literal key when present: SentinelOne PowerQuery (and other
 * SIEM-passthrough sources) return columns like "device.hostname" as flat
 * top-level keys, not nested objects. Falls back to nested traversal otherwise.
 */
export function getNestedValue(obj: any, path: string): any {
  if (obj != null && typeof obj === 'object' && Object.prototype.hasOwnProperty.call(obj, path)) {
    return obj[path];
  }
  return path.split('.').reduce((o, k) => o?.[k], obj);
}

/**
 * Infer a field's data type from a sample value.
 */
export function inferFieldType(value: any): string {
  if (value == null) return 'null';
  if (typeof value === 'boolean') return 'boolean';
  if (typeof value === 'number') return 'number';
  if (Array.isArray(value)) return 'array';
  if (typeof value === 'object') return 'object';
  const str = String(value).trim();
  if (/^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/.test(str)) return 'ip_address';
  if (/^([0-9a-fA-F]{2}[:\-.]){5}[0-9a-fA-F]{2}$/.test(str)) return 'mac_address';
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(str)) return 'uuid';
  if (/^\d{4}-\d{2}-\d{2}/.test(str)) return 'datetime';
  if (/^https?:\/\//i.test(str)) return 'url';
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(str)) return 'email';
  if (/^-?\d+(\.\d+)?$/.test(str) && str.length < 15) return 'number';
  return 'string';
}

/**
 * Color map for field type badges.
 */
export const TYPE_COLORS: Record<string, string> = {
  string: '#3b82f6',
  number: '#f59e0b',
  boolean: '#10b981',
  array: '#8b5cf6',
  object: '#ef4444',
  null: '#6b7280',
  datetime: '#ec4899',
  ip_address: '#06b6d4',
  mac_address: '#0d9488',
  uuid: '#7c3aed',
  url: '#2563eb',
  email: '#d946ef',
};

const TYPE_COLOR_DEFAULT = '#6b7280';

export function typeBadgeStyle(type: string): { background: string; color: string } {
  const color = TYPE_COLORS[type] ?? TYPE_COLOR_DEFAULT;
  return { background: `${color}20`, color };
}

export function getCoverageColor(pct: number): string {
  if (pct >= 90) return '#10b981';
  if (pct >= 50) return '#f59e0b';
  return '#ef4444';
}

/**
 * Return a human-readable label for a non-direct source method.
 * Returns null for 'direct' or empty values (nothing to show).
 */
export function getSourceMethodLabel(sourceMethod: string): string | null {
  if (!sourceMethod || sourceMethod === 'direct') return null;
  const labels: Record<string, string> = {
    splunk: 'Splunk',
    log_analytics: 'Azure Log Analytics',
    sentinelone_siem: 'SentinelOne SIEM',
    databricks: 'Databricks',
  };
  return labels[sourceMethod] ?? sourceMethod;
}

export function getSourceMethodLogo(sourceMethod: string): string | null {
  if (!sourceMethod || sourceMethod === 'direct') return null;
  const logos: Record<string, string> = {
    splunk: 'splunk.png',
    log_analytics: 'MS-Log-Analytics-Workspaces.svg',
    sentinelone_siem: 'microsoft sentinel.png',
    databricks: 'Databricks.svg',
  };
  return logos[sourceMethod] ?? null;
}
