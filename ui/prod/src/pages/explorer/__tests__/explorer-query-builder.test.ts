// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from 'vitest';

import { buildFilterToken, appendFilterToken, canAddToSearch } from '../explorer-query-builder';
import { parseSearchQuery } from '../explorer-query-parser';

// These helpers are the inverse of parseSearchQuery: the "add to search"
// right-click builds a query-text token that must round-trip back through the
// parser to the exact eq filter the user clicked. The round-trip assertions
// below are the point — a token that renders but parses to the wrong field/op/
// value would silently filter the wrong thing.
describe('buildFilterToken', () => {
  it('builds an unquoted "Field = value" token that round-trips to an eq filter', () => {
    const token = buildFilterToken('Category', 'Workstation');
    expect(token).toBe('Category = Workstation');
    expect(parseSearchQuery(token).filters).toEqual([
      { field: 'category', op: 'eq', value: 'Workstation' },
    ]);
  });

  it('quotes a field name containing spaces (multi-value field) and round-trips', () => {
    // Data Source is a multi-value field; eq on an array means "has" (membership).
    const token = buildFilterToken('Data Source', 'Splunk');
    expect(token).toBe('"Data Source" = Splunk');
    expect(parseSearchQuery(token).filters).toEqual([
      { field: 'data source', op: 'eq', value: 'Splunk' },
    ]);
  });

  it('quotes a value containing spaces so it parses back as one value', () => {
    const token = buildFilterToken('Model', 'OptiPlex 7090');
    expect(token).toBe('Model = "OptiPlex 7090"');
    expect(parseSearchQuery(token).filters).toEqual([
      { field: 'model', op: 'eq', value: 'OptiPlex 7090' },
    ]);
  });

  it('leaves a value without spaces unquoted', () => {
    expect(buildFilterToken('IP Address', '10.0.0.1')).toBe('"IP Address" = 10.0.0.1');
  });

  it('builds a not-equals token that round-trips to a neq filter', () => {
    // Exclude action: != is neq, which on an array field means "doesn't have".
    const token = buildFilterToken('Category', 'Workstation', '!=');
    expect(token).toBe('Category != Workstation');
    expect(parseSearchQuery(token).filters).toEqual([
      { field: 'category', op: 'neq', value: 'Workstation' },
    ]);
  });

  it('quotes field and value for a not-equals token too', () => {
    const token = buildFilterToken('Data Source', 'Splunk Cloud', '!=');
    expect(token).toBe('"Data Source" != "Splunk Cloud"');
    expect(parseSearchQuery(token).filters).toEqual([
      { field: 'data source', op: 'neq', value: 'Splunk Cloud' },
    ]);
  });
});

describe('appendFilterToken', () => {
  it('returns the token alone when the existing query is empty or blank', () => {
    expect(appendFilterToken('', 'Category = Workstation')).toBe('Category = Workstation');
    expect(appendFilterToken('   ', 'Category = Workstation')).toBe('Category = Workstation');
  });

  it('appends after existing query text separated by a single space', () => {
    expect(appendFilterToken('os ~ windows', 'Category = Workstation')).toBe(
      'os ~ windows Category = Workstation',
    );
  });

  it('is a no-op when the same filter is already present (dedupe)', () => {
    expect(appendFilterToken('Category = Workstation', 'Category = Workstation')).toBe(
      'Category = Workstation',
    );
  });

  it('dedupes by parsed filter, not raw string, so quoting differences still match', () => {
    // Existing uses the bare field token; the new token quotes the same field.
    const token = buildFilterToken('Category', 'Workstation'); // "Category = Workstation"
    expect(appendFilterToken('category = Workstation', token)).toBe('category = Workstation');
  });

  it('appends a different value for the same field (intentionally does not merge)', () => {
    // Two eq filters on one field AND together; we surface that rather than
    // silently merging, so the user can edit it down. Documents the design.
    expect(appendFilterToken('Category = Workstation', 'Category = Server')).toBe(
      'Category = Workstation Category = Server',
    );
  });

  it('keeps eq and neq of the same field/value as distinct filters', () => {
    // Add then Exclude the same value differ by operator, so neither dedupes.
    const include = buildFilterToken('Category', 'Workstation');
    const exclude = buildFilterToken('Category', 'Workstation', '!=');
    expect(appendFilterToken(include, exclude)).toBe(
      'Category = Workstation Category != Workstation',
    );
  });
});

describe('canAddToSearch', () => {
  it('enables text-like and multi-value columns whose cell shows the stored value', () => {
    expect(canAddToSearch('string')).toBe(true);
    expect(canAddToSearch('string_array')).toBe(true);
    expect(canAddToSearch('jsonb_array')).toBe(true);
  });

  it('disables columns whose displayed text differs from the stored value', () => {
    // An eq filter on the shown text ("2026-06-01", "High", "Supported", "Yes")
    // would match nothing because the stored value differs — so the action is
    // hidden rather than offered broken.
    expect(canAddToSearch('timestamp')).toBe(false);
    expect(canAddToSearch('date')).toBe(false);
    expect(canAddToSearch('boolean')).toBe(false);
    expect(canAddToSearch('float')).toBe(false);
    expect(canAddToSearch('integer')).toBe(false);
    expect(canAddToSearch('jsonb')).toBe(false);
  });
});
