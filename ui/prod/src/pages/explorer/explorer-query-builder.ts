// SPDX-License-Identifier: AGPL-3.0-or-later
// Explorer query builder — the inverse of explorer-query-parser. Turns a
// (field, value) pair clicked in the table into a search-query token, and
// appends it to the current query. Pure TypeScript, zero React/DOM deps.
//
// Quoting mirrors SearchBar's suggestion insertion exactly (field: quote when
// it has non-identifier chars; value: quote when it contains a space) so a
// right-click produces the same text a user would get by picking the field,
// its membership operator, and the value from the suggestion dropdown.

import { parseSearchQuery, type ParsedFilter } from './explorer-query-parser';

// Data types whose table cell renders the stored value verbatim, so an eq
// filter on what the user sees will actually match. Excludes date/timestamp,
// boolean, confidence (float) and numeric columns where the displayed text is
// formatted away from the stored value, and JSONB scalars.
const ADDABLE_DATA_TYPES = new Set(['string', 'string_array', 'jsonb_array']);

/** Whether a column of this data type supports the "add to search" action. */
export function canAddToSearch(dataType: string): boolean {
  return ADDABLE_DATA_TYPES.has(dataType);
}

/** Quote a field display name iff it contains non-identifier characters. */
function quoteField(displayName: string): string {
  return /[^a-zA-Z0-9_]/.test(displayName)
    ? `"${displayName.replace(/"/g, '\\"')}"`
    : displayName;
}

/** Quote a value iff it contains a space (matching SearchBar value insertion). */
function quoteValue(value: string): string {
  if (!value.includes(' ')) return value;
  const escaped = value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `"${escaped}"`;
}

/**
 * Build a `Field <op> value` search token. `op` is an operator symbol: `=`
 * (eq — exact membership / "has" on array fields) or `!=` (neq — "doesn't
 * have" on array fields). Defaults to `=`.
 */
export function buildFilterToken(
  displayName: string,
  value: string,
  op: string = '=',
): string {
  return `${quoteField(displayName)} ${op} ${quoteValue(value)}`;
}

function filterValueEqual(
  a: ParsedFilter['value'],
  b: ParsedFilter['value'],
): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  return a === b;
}

/**
 * Append `token` to `query`, separated by a space. No-op when the token's
 * parsed filter already exists in the query (structural dedupe — so re-adding
 * the same value, regardless of quoting, does nothing). Different values for
 * the same field are intentionally NOT merged; both are kept so the user sees
 * and can edit the conjunction.
 */
export function appendFilterToken(query: string, token: string): string {
  const trimmed = query.trim();
  if (!trimmed) return token;

  const newFilters = parseSearchQuery(token).filters;
  if (newFilters.length === 1) {
    const nf = newFilters[0];
    const existing = parseSearchQuery(trimmed).filters;
    const duplicate = existing.some(
      (f) =>
        f.field === nf.field &&
        f.op === nf.op &&
        filterValueEqual(f.value, nf.value),
    );
    if (duplicate) return trimmed;
  }

  return `${trimmed} ${token}`;
}
