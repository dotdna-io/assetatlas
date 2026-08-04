// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import { evaluateFilter } from '../DataFiltersStep';

// H-1: the wizard's client-side preview must match server-side ingest semantics
// (data_filters -> operator registry match(), which is case-insensitive), so the
// previewed match count agrees with what is actually ingested.
describe('DataFiltersStep evaluateFilter — case-insensitive (matches ingest)', () => {
  const rec = { os: 'Windows Server', tag: 'PROD' };

  it.each([
    ['equals', 'windows server', true],
    ['equals', 'WINDOWS SERVER', true],
    ['not_equals', 'windows server', false],
    ['contains', 'WINDOWS', true],
    ['not_contains', 'windows', false],
    ['starts_with', 'WIN', true],
    ['ends_with', 'SERVER', true],
  ])('os %s %s -> %s regardless of case', (operator, value, expected) => {
    expect(evaluateFilter(rec, { field: 'os', operator, value })).toBe(expected);
  });

  it('contains matches a differently-cased tag (the divergence H-1 fixes)', () => {
    // Server ingest would include this record; the pre-fix case-sensitive preview
    // excluded it, so the preview count disagreed with the ingest count.
    expect(evaluateFilter(rec, { field: 'tag', operator: 'contains', value: 'prod' })).toBe(true);
  });

  it('numeric comparisons are unaffected', () => {
    expect(evaluateFilter({ n: '10' }, { field: 'n', operator: 'greater_than', value: '5' })).toBe(true);
    expect(evaluateFilter({ n: '3' }, { field: 'n', operator: 'less_than', value: '5' })).toBe(true);
  });
});

describe('DataFiltersStep evaluateFilter — boolean ops mirror server (H-1 round 2)', () => {
  it.each([
    ['is_true', 'TRUE', true],
    ['is_true', 'True', true],
    ['is_true', 'true', true],
    ['is_true', true, true],
    ['is_true', 'false', false],
    ['is_true', false, false],
    ['is_false', 'FALSE', true],
    ['is_false', false, true],
    ['is_false', 'true', false],
  ])('%s on %p -> %s case-insensitively', (operator, value, expected) => {
    expect(evaluateFilter({ flag: value }, { field: 'flag', operator })).toBe(expected);
  });
});
