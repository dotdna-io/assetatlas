// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import { parseSearchQuery, OPERATOR_ALIASES } from '../explorer-query-parser';

// C-1: alphabetic operator aliases must not match INSIDE an ordinary word.
// Pre-fix, the greedy field token backtracks so "berlin" -> field "berl" + the
// "in" alias of `any`, turning plain keyword searches into garbage filters.
describe('parseSearchQuery — word-internal alias mis-split (C-1)', () => {
  it('"berlin servers" stays a keyword (not field=berl op=any)', () => {
    const r = parseSearchQuery('berlin servers');
    expect(r.filters).toHaveLength(0);
    expect(r.keyword).toBe('berlin servers');
  });

  it('"machine learning" stays a keyword (not the `ne` alias inside machine)', () => {
    const r = parseSearchQuery('machine learning');
    expect(r.filters).toHaveLength(0);
    expect(r.keyword).toBe('machine learning');
  });

  it('"chassis inventory" stays a keyword (not the `is` alias inside chassis)', () => {
    const r = parseSearchQuery('chassis inventory');
    expect(r.filters).toHaveLength(0);
  });

  it('a real spaced word operator still parses', () => {
    const r = parseSearchQuery('category is Server');
    expect(r.filters[0]).toMatchObject({ op: 'eq', value: 'Server' });
  });

  it('a spaced symbol operator still parses', () => {
    const r = parseSearchQuery('confidence >= 0.8');
    expect(r.filters[0]).toMatchObject({ op: 'gte', value: '0.8' });
  });

  it('a symbol operator may abut the field (no leading space) unlike word ops', () => {
    const r = parseSearchQuery('category= Server');
    expect(r.filters[0]).toMatchObject({ op: 'eq', value: 'Server' });
  });
});

// C-2: the search bar autocompletes BETWEEN/WITHIN for date/number fields; the
// parser must be able to round-trip them.
describe('parseSearchQuery — between / within_last (C-2)', () => {
  it('parses a lowercase "between" into a two-value list', () => {
    const r = parseSearchQuery('"Last Seen" between 2024-01-01,2024-06-01');
    expect(r.filters[0].op).toBe('between');
    expect(r.filters[0].value).toEqual(['2024-01-01', '2024-06-01']);
  });

  it('parses the inserted BETWEEN symbol (uppercase)', () => {
    const r = parseSearchQuery('confidence BETWEEN 0.2,0.8');
    expect(r.filters[0].op).toBe('between');
    expect(r.filters[0].value).toEqual(['0.2', '0.8']);
  });

  it('parses "within" into within_last with a single value', () => {
    const r = parseSearchQuery('last_seen within 30d');
    expect(r.filters[0]).toMatchObject({ field: 'last_seen', op: 'within_last', value: '30d' });
  });

  it('does not mangle the "in" inside "within" into the any alias', () => {
    const r = parseSearchQuery('last_seen within 30d');
    expect(r.filters).toHaveLength(1);
    expect(r.filters[0].op).toBe('within_last');
  });

  it('exposes between/within_last in the alias table', () => {
    expect(OPERATOR_ALIASES['between']).toBe('between');
    expect(OPERATOR_ALIASES['within']).toBe('within_last');
  });
});

// The search bar autocompletes the EMPTY / !EMPTY symbols for the empty /
// notempty operators (same class as the BETWEEN/WITHIN symbol round-trip above).
// !EMPTY regressed: '!empty' — the lowercased inserted symbol — was missing from
// notempty's aliases, so picking "is not empty" from the dropdown produced an
// unparseable token that fell through to the free-text keyword instead of a
// filter (EMPTY worked only because its symbol lowercases to the 'empty' alias).
describe('parseSearchQuery — empty / notempty inserted symbols', () => {
  it('parses the inserted EMPTY symbol into the empty operator', () => {
    const r = parseSearchQuery('hostname EMPTY');
    expect(r.filters).toHaveLength(1);
    expect(r.filters[0]).toMatchObject({ field: 'hostname', op: 'empty', value: null });
    expect(r.keyword).toBe('');
  });

  it('parses the inserted !EMPTY symbol into the notempty operator', () => {
    const r = parseSearchQuery('hostname !EMPTY');
    expect(r.filters).toHaveLength(1);
    expect(r.filters[0]).toMatchObject({ field: 'hostname', op: 'notempty', value: null });
    expect(r.keyword).toBe('');
  });
});

// Dashboard confidence-chevron drill-downs drop a generated query string into
// the search box: a category condition followed by a Confidence Level band,
// space-separated (no "AND" keyword — that's the explorer's implicit default).
// The multi-word field must stay quoted so the whole string re-parses into the
// two conditions the drill-down intended, not field "Level" + stray "Confidence".
describe('parseSearchQuery — confidence chevron drill-down round-trip', () => {
  it('round-trips "Category = server \\"Confidence Level\\" = high" into two filters', () => {
    const r = parseSearchQuery('Category = server "Confidence Level" = high');
    expect(r.filters).toHaveLength(2);
    expect(r.filters).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'category', op: 'eq', value: 'server' }),
        expect.objectContaining({ field: 'confidence level', op: 'eq', value: 'high' }),
      ]),
    );
    expect(r.keyword).toBe('');
  });

  it('the same query WITHOUT quotes mis-parses the field — proving the quotes matter', () => {
    const r = parseSearchQuery('Category = server Confidence Level = high');
    // "Confidence" can no longer attach to "Level" as one field, so the band
    // condition does not come through as field "confidence level".
    expect(r.filters).not.toContainEqual(
      expect.objectContaining({ field: 'confidence level', op: 'eq', value: 'high' }),
    );
  });
});
