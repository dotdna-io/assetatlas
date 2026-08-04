// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import { parseSearchQuery, OPERATOR_ALIASES, OPERATOR_SYMBOLS } from '../explorer-query-parser';

describe('explorer-query-parser operator tables from specs', () => {
  it('resolves symbol aliases', () => {
    const r = parseSearchQuery('category = Server');
    expect(r.filters[0]).toMatchObject({ op: 'eq', value: 'Server' });
  });

  it('resolves IN to any', () => {
    const r = parseSearchQuery('category IN a,b');
    expect(r.filters[0].op).toBe('any');
  });

  it('resolves none/without aliases', () => {
    const r = parseSearchQuery('"Data Source" none jamf,sccm');
    expect(r.filters[0].op).toBe('none');
  });

  it('resolves all/every aliases', () => {
    const r = parseSearchQuery('"Data Source" all jamf,sccm');
    expect(r.filters[0].op).toBe('all');
  });

  it('exposes a symbol table for every canonical code', () => {
    for (const code of ['eq', 'neq', 'contains', 'starts', 'ends', 'any', 'all', 'none', 'empty', 'notempty']) {
      expect(OPERATOR_SYMBOLS[code]).toBeTruthy();
    }
  });

  it('exposes an alias->code table including legacy in', () => {
    expect(OPERATOR_ALIASES['in']).toBe('any');
    expect(OPERATOR_ALIASES['without']).toBe('none');
  });
});

describe('parseSearchQuery — any/all/none multi-word values (limitations §15)', () => {
  it('keeps a quoted multi-word value whole', () => {
    // Regression for the §15 bug: rawValue.split(/[\s,]+/) shredded
    // "Network Device" into ['"Network', 'Device",']. WHY it matters: fields
    // like category/os/manufacturer carry multi-word values, so any/all/none
    // were unusable on them.
    const { filters } = parseSearchQuery('category any "Network Device", Server');
    expect(filters).toHaveLength(1);
    expect(filters[0].field).toBe('category');
    expect(filters[0].op).toBe('any');
    expect(filters[0].value).toEqual(['Network Device', 'Server']);
  });

  it('keeps multiple quoted multi-word values whole', () => {
    const { filters } = parseSearchQuery('os any "Windows 10" "Windows 11"');
    expect(filters[0].value).toEqual(['Windows 10', 'Windows 11']);
  });

  it('still splits a plain space-separated list', () => {
    const { filters } = parseSearchQuery('category any Server Workstation');
    expect(filters[0].value).toEqual(['Server', 'Workstation']);
  });

  it('still splits a plain comma-separated list (no regression)', () => {
    // The existing `IN a,b` path relies on comma splitting; the quote-aware
    // fix must not regress it (tokenizeQuery treats commas as literal, so the
    // fix splits each unquoted token on commas itself).
    const { filters } = parseSearchQuery('category any Server,Workstation');
    expect(filters[0].value).toEqual(['Server', 'Workstation']);
  });

  it('strips quotes from a comma-adjacent quoted value with no space', () => {
    // `"Server","Workstation"` (comma directly abutting the quotes) — the
    // tokenizer treats the post-comma quote as a literal char, so the split
    // would leave '"Workstation"'; the wrapping quotes must be stripped.
    const { filters } = parseSearchQuery('category any "Server","Workstation"');
    expect(filters[0].value).toEqual(['Server', 'Workstation']);
  });
});
