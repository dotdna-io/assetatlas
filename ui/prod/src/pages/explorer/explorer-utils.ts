// SPDX-License-Identifier: AGPL-3.0-or-later
// Explorer utility functions — pure TypeScript, zero React/DOM dependencies.

import type { FieldCatalogEntry } from '@/types/field-id';

// ---------------------------------------------------------------------------
// Field catalog grouping
// ---------------------------------------------------------------------------

/** Return the originating source/group name for an extended_attribute /
 * integration_group entry; null for other kinds. */
export function getEntrySource(e: FieldCatalogEntry): string | null {
  if (e.field_id.kind === 'extended_attribute') return e.field_id.source;
  if (e.field_id.kind === 'integration_group') return e.field_id.group;
  return null;
}

/** Build an `encoded_key → entry` lookup from a flat catalog list. */
export function buildFieldMap(
  entries: FieldCatalogEntry[] | undefined,
): Record<string, FieldCatalogEntry> {
  const map: Record<string, FieldCatalogEntry> = {};
  if (!entries) return map;
  for (const e of entries) map[e.encoded_key] = e;
  return map;
}

// A field's Explorer display label is ALWAYS its catalog canonical name
// (display_name, or `name` for the column-spec shape). There is deliberately
// no label-override layer: the search bar inserts getDisplayName(field) into
// the query string and buildFilters resolves it back through findFieldEntry,
// which matches only on encoded_key or display_name. Any label that isn't the
// field's display_name fails to resolve, so the filter is silently dropped.
// Short overrides did exactly that — 'Confidence' for two confidence fields and
// 'Asset' for asset_label all made their search a no-op (#704). If a friendlier
// label is ever needed, make the round-trip work (e.g. insert the encoded_key)
// rather than reintroducing a non-resolving override here.
export function getDisplayName(
  field: { display_name?: string; name?: string },
): string {
  return field.display_name ?? field.name ?? '';
}

/** Resolve any field token (encoded key, display name, or column name) to its
 *  catalog entry. Used by the search-bar parser path and the column-filter
 *  builder, both of which receive heterogeneous tokens depending on whether
 *  the user typed, picked from the dropdown, or clicked a column header. */
export function findFieldEntry(
  token: string,
  entries: FieldCatalogEntry[] | Record<string, FieldCatalogEntry>,
): FieldCatalogEntry | null {
  if (!token) return null;
  const list = Array.isArray(entries) ? entries : Object.values(entries);
  if (!Array.isArray(entries) && entries[token]) return entries[token];
  const lower = token.toLowerCase();
  for (const e of list) {
    if (e.encoded_key === token) return e;
    if (e.encoded_key.toLowerCase() === lower) return e;
    if (e.display_name.toLowerCase() === lower) return e;
  }
  return null;
}

const DATE_DATA_TYPES = new Set(['timestamp', 'date', 'datetime']);

/** Does this column represent a timestamp/date value? Checks the catalog
 *  data_type first; falls back to known encoded-key literals for when the
 *  catalog hasn't loaded yet. */
export function isDateField(
  encodedKey: string,
  dataType: string | undefined,
): boolean {
  if (dataType && DATE_DATA_TYPES.has(dataType)) return true;
  return (
    encodedKey === 'static:Last%20Seen' ||
    encodedKey === 'static:First%20Seen'
  );
}

/**
 * Group a flat catalog-entries list into the buckets the legacy Explorer UI
 * expects: standard, advanced, extended (grouped by source), tags, posture.
 * Categories `target`, `enrichment`, `virtual`, and `unified` all roll up into
 * `standard`.
 */
export function groupByCategory(entries: FieldCatalogEntry[]): {
  standard: FieldCatalogEntry[];
  advanced: FieldCatalogEntry[];
  extended: Record<string, FieldCatalogEntry[]>;
  tags: FieldCatalogEntry[];
  posture: FieldCatalogEntry[];
} {
  const standard: FieldCatalogEntry[] = [];
  const advanced: FieldCatalogEntry[] = [];
  const extended: Record<string, FieldCatalogEntry[]> = {};
  const tags: FieldCatalogEntry[] = [];
  const posture: FieldCatalogEntry[] = [];

  for (const e of entries) {
    switch (e.category) {
      case 'target':
      case 'enrichment':
      case 'virtual':
      case 'unified':
        standard.push(e);
        break;
      case 'advanced':
        advanced.push(e);
        break;
      case 'extended_attributes': {
        const src = getEntrySource(e) ?? 'Other';
        (extended[src] ??= []).push(e);
        break;
      }
      case 'tags':
        tags.push(e);
        break;
      case 'posture':
        posture.push(e);
        break;
    }
  }
  return { standard, advanced, extended, tags, posture };
}

// ---------------------------------------------------------------------------
// Cell formatting
// ---------------------------------------------------------------------------

/**
 * Format a cell value for display in the Explorer table.
 * Mirrors the legacy Explorer.table.formatCellValue function.
 *
 * Handled data types: string, string_array, timestamp, date, boolean, float,
 * numeric, category, confidence, os_eol_status, last_seen.
 *
 * Returns a plain string (no HTML). Callers are responsible for escaping if
 * the result is inserted into innerHTML.
 */
export function formatCellValue(
  value: unknown,
  dataType: string,
  fieldKey: string,
): string {
  if (value === null || value === undefined) {
    return '-';
  }

  // String arrays — join first 3 with comma, show overflow count
  if (dataType === 'string_array' || Array.isArray(value)) {
    const arr = Array.isArray(value) ? value : [value];
    if (arr.length === 0) return '-';
    if (arr.length <= 3) return arr.join('; ');
    return arr.slice(0, 3).join('; ') + ` (+${arr.length - 3})`;
  }

  // Timestamps and date-like columns. fieldKey may be a legacy short name
  // ("last_seen") or an encoded FieldId ("static:Last%20Seen"); match both.
  const isLastSeen = fieldKey === 'last_seen' || fieldKey === 'static:Last%20Seen';
  const isFirstSeen = fieldKey === 'first_seen' || fieldKey === 'static:First%20Seen';
  if (dataType === 'timestamp' || isLastSeen || isFirstSeen) {
    return formatDate(String(value));
  }

  // Date (date-only, no time component)
  if (dataType === 'date') {
    if (!value) return '-';
    return formatDate(String(value));
  }

  // Boolean
  if (dataType === 'boolean' || typeof value === 'boolean') {
    return value ? 'Yes' : 'No';
  }

  // Numeric types — strip unnecessary decimals
  if (dataType === 'float' || dataType === 'numeric') {
    const num = parseFloat(String(value));
    if (isNaN(num)) return String(value);
    return num % 1 === 0 ? num.toString() : num.toFixed(2);
  }

  // Confidence — stored as 0-1 float, displayed as percentage
  if (
    dataType === 'confidence' ||
    fieldKey === 'confidence' ||
    fieldKey === 'static:Confidence%20Score'
  ) {
    const num = parseFloat(String(value));
    if (isNaN(num)) return String(value);
    return `${(num * 100).toFixed(0)}%`;
  }

  // Fallback: stringify
  return String(value);
}

// ---------------------------------------------------------------------------
// Date utilities
// ---------------------------------------------------------------------------

/**
 * Format an ISO date/datetime string as YYYY-MM-DD.
 * Returns the original string if parsing fails.
 */
export function formatDate(dateStr: string): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const yr = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const dy = String(d.getDate()).padStart(2, '0');
  return `${yr}-${mo}-${dy}`;
}

/**
 * Return the number of whole days between the given date string and now.
 * Returns 0 for invalid input.
 */
export function daysSince(dateStr: string): number {
  if (!dateStr) return 0;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return 0;
  const diffMs = Date.now() - d.getTime();
  return Math.floor(diffMs / (1000 * 60 * 60 * 24));
}

/**
 * Classify the last-seen date as recent, aging, or stale.
 * recent:  0–7 days
 * aging:   8–30 days
 * stale:   31+ days
 */
export function lastSeenStatus(dateStr: string): 'recent' | 'aging' | 'stale' {
  const days = daysSince(dateStr);
  if (days > 30) return 'stale';
  if (days > 7) return 'aging';
  return 'recent';
}

// ---------------------------------------------------------------------------
// Confidence-band drill-down helpers
// ---------------------------------------------------------------------------

export type ConfidenceBand = 'confirmed' | 'under_covered' | 'shadows';

export interface ConfidenceBandFilter {
  /** Structured filter — field is the display name accepted by findFieldEntry. */
  filter: { field: string; op: string; value: string };
  /** Human-readable label for the search bar / breadcrumb. */
  label: string;
}

/**
 * Map a confidence band name (from the `?confidence=` URL param) to the
 * structured filter and display label for the `confidence_level` virtual field.
 *
 * The filter uses the display-name token `'Confidence Level'` (resolved by
 * findFieldEntry via `e.display_name.toLowerCase()`) and the `eq` operator —
 * the only single-value operator the virtual field supports.  Values are the
 * lowercase tier strings the backend `_build_confidence_level_clause` expects.
 *
 * Returns null for unknown band values so callers can safely ignore them.
 */
export function confidenceBandToFilter(
  band: string,
): ConfidenceBandFilter | null {
  // The field name has a space, so it must be quoted in the search-query text
  // for the parser to read it as one field (the unquoted form allows no
  // spaces). The structured filter keeps the bare field name.
  switch (band) {
    case 'confirmed':
      return {
        filter: { field: 'Confidence Level', op: 'eq', value: 'high' },
        label: '"Confidence Level" = high',
      };
    case 'under_covered':
      return {
        filter: { field: 'Confidence Level', op: 'eq', value: 'medium' },
        label: '"Confidence Level" = medium',
      };
    case 'shadows':
      return {
        filter: { field: 'Confidence Level', op: 'eq', value: 'low' },
        label: '"Confidence Level" = low',
      };
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// CSV generation
// ---------------------------------------------------------------------------

/**
 * Escape a single value per RFC 4180.
 * Wraps in double-quotes if the value contains a comma, double-quote, or newline.
 */
// Defuse spreadsheet formula / CSV injection: prefix a single quote when a cell
// could be executed as a formula on open (Excel / Sheets / LibreOffice). A
// leading = + @ TAB or CR always triggers; a leading '-' triggers only when it
// is NOT a negative number (not followed by an ASCII digit or '.'), so -5 / -5.3
// / -.5 stay numeric while -cmd / -=1+1 are defused. A lone '-' is left as-is.
// Known accepted residual: a formula starting minus-then-digit (e.g. -2+3) reads
// as numeric and is NOT defused — the price of keeping negative numbers intact.
// MUST stay byte-identical to the backend _neutralize_formula
// (src/api/utils/csv_export.py); the digit test is ASCII-only to match Python's.
function neutralizeFormula(s: string): string {
  if (!s) return s;
  const c0 = s[0];
  if (c0 === '=' || c0 === '+' || c0 === '@' || c0 === '\t' || c0 === '\r') {
    return `'${s}`;
  }
  if (c0 === '-') {
    const nxt = s.length > 1 ? s[1] : '';
    if (nxt === '' || (nxt >= '0' && nxt <= '9') || nxt === '.') return s;
    return `'${s}`;
  }
  return s;
}

function escapeCSVValue(val: unknown): string {
  if (val === null || val === undefined) return '';
  const str = neutralizeFormula(String(val));
  if (str.includes(',') || str.includes('"') || str.includes('\n') || str.includes('\r')) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/**
 * Generate an RFC 4180-compliant CSV string from a list of records.
 *
 * - Arrays are joined with "; "
 * - ISO timestamp strings are output as-is (already ISO)
 * - All other values are stringified and escaped
 *
 * @param data    Row data — each element is a flat record of field -> value.
 * @param columns Column definitions specifying which fields to include and their header labels.
 */
export function generateCSV(
  data: Record<string, unknown>[],
  columns: { key: string; name: string }[],
): string {
  const header = columns.map((c) => escapeCSVValue(c.name)).join(',');

  const rows = data.map((row) => {
    return columns
      .map((col) => {
        const val = row[col.key];
        if (Array.isArray(val)) {
          return escapeCSVValue(val.join('; '));
        }
        return escapeCSVValue(val);
      })
      .join(',');
  });

  return [header, ...rows].join('\r\n');
}
