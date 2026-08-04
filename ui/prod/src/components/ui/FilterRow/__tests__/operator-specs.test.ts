// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import type { FieldCatalogEntry, OperatorSpec } from '@/types/field-id';
import { deriveOperatorSpecs, operatorLabel, isFilterValueReady } from '../operator-specs';

function entry(partial: Partial<FieldCatalogEntry>): FieldCatalogEntry {
  return {
    display_name: 'Data Source',
    field_id: { kind: 'static', name: 'Data Source' },
    encoded_key: 'static:Data%20Source',
    category: 'target',
    data_type: 'string_array',
    // operator_specs is the only operator source (the deprecated operators[]
    // field + its legacy fallback were removed in Phase 3). Tests override it.
    operator_specs: [],
    is_array: true,
    is_grouped: false,
    member_sources: null,
    ...partial,
  };
}

const specs: OperatorSpec[] = [
  { code: 'eq', label: 'has', symbol: '=', aliases: ['=', 'is', 'equals'], arity: 'one', value_type: 'text' },
  { code: 'any', label: 'has any of', symbol: 'IN', aliases: ['in'], arity: 'list', value_type: 'text' },
  { code: 'empty', label: 'is empty', symbol: 'EMPTY', aliases: [], arity: 'none', value_type: 'text' },
];

describe('deriveOperatorSpecs', () => {
  it('prefers operator_specs verbatim and preserves order (element 0 = default)', () => {
    const out = deriveOperatorSpecs(entry({ operator_specs: specs }));
    expect(out.map((s) => s.code)).toEqual(['eq', 'any', 'empty']);
    expect(out[0].code).toBe('eq');
  });

  it('returns [] when operator_specs is absent (no legacy fallback)', () => {
    // Phase 3 removed the deprecated operators[] field and its legacySpec
    // fallback — every real entry now carries operator_specs (the API populates
    // it; reporting's QuestionRenderer synthesizes it directly).
    expect(deriveOperatorSpecs(entry({ operator_specs: undefined }))).toEqual([]);
    expect(deriveOperatorSpecs(entry({ operator_specs: [] }))).toEqual([]);
  });
});

describe('isFilterValueReady', () => {
  it('valueless ops are always ready', () => {
    expect(isFilterValueReady('empty', '')).toBe(true);
    expect(isFilterValueReady('notempty', '')).toBe(true);
    expect(isFilterValueReady('is_empty', undefined)).toBe(true);
  });

  it('a scalar op needs a non-empty value', () => {
    expect(isFilterValueReady('eq', '')).toBe(false);
    expect(isFilterValueReady('eq', '   ')).toBe(false);
    expect(isFilterValueReady('eq', 'Server')).toBe(true);
  });

  it('between needs BOTH bounds of "lo,hi"', () => {
    expect(isFilterValueReady('between', '')).toBe(false);
    expect(isFilterValueReady('between', '2026-01-01,')).toBe(false); // hi missing
    expect(isFilterValueReady('between', ',2026-06-01')).toBe(false); // lo missing
    expect(isFilterValueReady('between', '2026-01-01,2026-06-01')).toBe(true);
  });

  it('within_last needs a numeric amount', () => {
    expect(isFilterValueReady('within_last', 'd')).toBe(false); // unit only, no amount
    expect(isFilterValueReady('within_last', '')).toBe(false);
    expect(isFilterValueReady('within_last', '30d')).toBe(true);
  });
});

describe('operatorLabel', () => {
  it('resolves a stored code to its spec label via the entry', () => {
    expect(operatorLabel('any', entry({ operator_specs: specs }))).toBe('has any of');
  });

  it('matches a code against an alias when not a direct code', () => {
    // Legacy stored "in" should display as the "any" spec label.
    expect(operatorLabel('in', entry({ operator_specs: specs }))).toBe('has any of');
  });

  it('falls back to the raw operator when no entry/spec resolves it', () => {
    expect(operatorLabel('eq', undefined)).toBe('eq');
    expect(operatorLabel('zzz', entry({ operator_specs: specs }))).toBe('zzz');
  });
});
