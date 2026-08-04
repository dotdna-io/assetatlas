// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from 'vitest';

import { buildOperatorSuggestionsForField, detectContext } from '../SearchBar';
import type { SuggestionContext } from '../SearchBar';
import type { FieldCatalogEntry } from '@/types/field-id';

// Mirrors the fieldKeySet the component builds from the field catalog: each
// entry's encoded_key plus its lower-cased display name. Multi-word display
// names ("Data Source", "OS Normalized") and encoded FieldIds only resolve
// through this set — they are not in the parser's STATIC_FIELD_MAPPINGS.
const fieldKeys = new Set<string>([
  'data source',
  'os normalized',
  'ip address',
  'manufacturer',
  'category',
  'hostname',
  'ext:intune:devicename',
]);

interface Case {
  name: string;
  query: string;
  cursor?: number; // defaults to end of query
  expected: Partial<SuggestionContext> & Pick<SuggestionContext, 'type'>;
}

// Characterization matrix: every branch of detectContext. All but the one
// flagged FIX case capture the pre-existing behavior, so the refactor to a
// quote-aware tokenizer must preserve them exactly.
const CASES: Case[] = [
  { name: 'empty input -> field', query: '', cursor: 0, expected: { type: 'field', partial: '', replaceStart: 0, replaceEnd: 0 } },
  { name: 'whitespace only -> field', query: '  ', expected: { type: 'field', partial: '', replaceStart: 0, replaceEnd: 2 } },
  { name: 'partial field, nothing before', query: 'host', expected: { type: 'field', partial: 'host', replaceStart: 0, replaceEnd: 4 } },
  { name: 'complete unquoted field, no trailing space (still typing)', query: 'hostname', expected: { type: 'field', partial: 'hostname', replaceStart: 0, replaceEnd: 8 } },
  { name: 'field + space -> operator', query: 'hostname ', expected: { type: 'operator', field: 'hostname', partial: '', replaceStart: 9, replaceEnd: 9 } },
  { name: 'field + partial operator', query: 'hostname =', expected: { type: 'operator', field: 'hostname', partial: '=', replaceStart: 9, replaceEnd: 10 } },
  { name: 'field + operator + space -> value', query: 'hostname = ', expected: { type: 'value', field: 'hostname', partial: '', replaceStart: 11, replaceEnd: 11 } },
  { name: 'field + operator + partial value', query: 'hostname = serv', expected: { type: 'value', field: 'hostname', partial: 'serv', replaceStart: 11, replaceEnd: 15 } },
  { name: 'empty operator completes the filter -> field', query: 'hostname empty ', expected: { type: 'field', partial: '', replaceStart: 15, replaceEnd: 15 } },
  { name: 'notempty operator completes the filter -> field', query: 'hostname notempty ', expected: { type: 'field', partial: '', replaceStart: 18, replaceEnd: 18 } },
  // Symbol forms — what the dropdown actually inserts (item.value is the operator
  // symbol). EMPTY works because it lowercases to the 'empty' alias; !EMPTY
  // regressed because '!empty' was missing from notempty's aliases, so it was
  // read as a stray token after the field and re-prompted for an operator.
  { name: 'empty SYMBOL completes the filter -> field', query: 'hostname EMPTY ', expected: { type: 'field', partial: '', replaceStart: 15, replaceEnd: 15 } },
  { name: 'notempty SYMBOL (!EMPTY) completes the filter -> field', query: 'hostname !EMPTY ', expected: { type: 'field', partial: '', replaceStart: 16, replaceEnd: 16 } },
  // A field added AFTER a valueless filter must suggest an OPERATOR for that new
  // field — not be swallowed as the valueless op's (non-existent) value, which
  // re-prompted for a field and duplicated it ("category category"). One case
  // per spelling: word alias, EMPTY symbol, and !EMPTY symbol.
  { name: 'new field after empty (word) filter -> operator', query: 'hostname empty category ', expected: { type: 'operator', field: 'category', partial: '', replaceStart: 24, replaceEnd: 24 } },
  { name: 'new field after EMPTY symbol filter -> operator', query: 'hostname EMPTY category ', expected: { type: 'operator', field: 'category', partial: '', replaceStart: 24, replaceEnd: 24 } },
  { name: 'new field after !EMPTY symbol filter -> operator', query: 'hostname !EMPTY category ', expected: { type: 'operator', field: 'category', partial: '', replaceStart: 25, replaceEnd: 25 } },
  { name: 'new partial field after a completed filter', query: 'category = Server ho', expected: { type: 'field', partial: 'ho', replaceStart: 18, replaceEnd: 20 } },
  { name: 'new field (space) after a completed filter', query: 'category = Server ', expected: { type: 'field', partial: '', replaceStart: 18, replaceEnd: 18 } },
  { name: 'inside unclosed double quote -> null', query: '"Data', cursor: 5, expected: { type: null, partial: '', replaceStart: 5, replaceEnd: 5 } },
  { name: 'inside unclosed single quote -> null', query: "'Data", cursor: 5, expected: { type: null, partial: '', replaceStart: 5, replaceEnd: 5 } },
  { name: 'quoted multi-word field + space -> operator', query: '"Data Source" ', expected: { type: 'operator', field: 'data source', partial: '', replaceStart: 14, replaceEnd: 14 } },
  { name: 'quoted field + operator + space -> value', query: '"Data Source" ~ ', expected: { type: 'value', field: 'data source', partial: '', replaceStart: 16, replaceEnd: 16 } },
  { name: 'quoted field + operator + partial value', query: '"Data Source" ~ Win', expected: { type: 'value', field: 'data source', partial: 'win', replaceStart: 16, replaceEnd: 19 } },
  { name: 'single-quoted multi-word field -> operator', query: "'Data Source' ", expected: { type: 'operator', field: 'data source', partial: '', replaceStart: 14, replaceEnd: 14 } },
  { name: 'quoted field after a completed filter -> operator', query: 'category = Server "Data Source" ', expected: { type: 'operator', field: 'data source', partial: '', replaceStart: 32, replaceEnd: 32 } },
  { name: 'snake_case unknown field -> operator', query: 'some_field ', expected: { type: 'operator', field: 'some_field', partial: '', replaceStart: 11, replaceEnd: 11 } },
  { name: 'unquoted multi-word field -> operator', query: 'os normalized ', expected: { type: 'operator', field: 'os normalized', partial: '', replaceStart: 14, replaceEnd: 14 } },
  { name: 'synonym field -> operator', query: 'ip ', expected: { type: 'operator', field: 'ip', partial: '', replaceStart: 3, replaceEnd: 3 } },
  { name: 'second field after a standard filter -> operator', query: 'manufacturer = Dell category ', expected: { type: 'operator', field: 'category', partial: '', replaceStart: 29, replaceEnd: 29 } },
  { name: 'encoded FieldId token -> operator', query: 'ext:Intune:deviceName ', expected: { type: 'operator', field: 'ext:intune:devicename', partial: '', replaceStart: 22, replaceEnd: 22 } },
  { name: 'IN multi-value then space -> field', query: 'category IN a,b ', expected: { type: 'field', partial: '', replaceStart: 16, replaceEnd: 16 } },
  // List operators (IN/ALL/NONE) accept a comma-separated list. A trailing comma
  // after the value(s) is the "add another value" signal insertSuggestion writes
  // after each pick — so the cursor stays in value mode for the SAME field
  // instead of prompting for a new field. WHY it matters: without this, picking
  // one value ends the filter and a list can't be built from the dropdown at all.
  { name: 'IN + first value context is a list value', query: '"Data Source" IN ', expected: { type: 'value', field: 'data source', partial: '', listValue: true } },
  { name: 'IN + value + trailing comma -> value (add another)', query: '"Data Source" IN Unifi, ', expected: { type: 'value', field: 'data source', partial: '', listValue: true } },
  { name: 'IN + partial next value after comma -> value', query: '"Data Source" IN Unifi, Waz', expected: { type: 'value', field: 'data source', partial: 'waz', listValue: true } },
  { name: 'IN + two values + trailing comma -> value', query: 'category IN a,b, ', expected: { type: 'value', field: 'category', partial: '', listValue: true } },
  // The comma is what keeps the list open; a value followed by a space with no
  // comma still completes the filter (unchanged from the case above).
  { name: 'IN + value + space (no comma) still completes -> field', query: '"Data Source" IN Unifi ', expected: { type: 'field', partial: '' } },
  // `between` is a list operator too, but exactly two bounds. The first bound
  // chains a comma to the second; the second completes (no third). WHY it
  // matters: the backend drops a `between` clause with fewer than two bounds, so
  // without chaining the second bound the whole filter silently vanishes.
  { name: 'BETWEEN first bound chains (listValue true)', query: 'confidence between ', expected: { type: 'value', field: 'confidence', partial: '', listValue: true } },
  { name: 'BETWEEN second bound after comma does not chain', query: 'confidence between 1, ', expected: { type: 'value', field: 'confidence', partial: '', listValue: false } },
  // The continuation must not reach BACK across a later, complete filter. A new
  // `field <op>` starting in the value region means the cursor is past the list,
  // so a stray trailing comma there must NOT resume the earlier list's values.
  // Both symbol operators (`=`) and word operators (`is`) count as boundaries.
  { name: 'list op then later SYMBOL-op filter (trailing comma) -> new field, not resumed list', query: 'category IN a, hostname = b, ', expected: { type: 'field', partial: '' } },
  { name: 'list op then later WORD-op filter (trailing comma) -> new field, not resumed list', query: 'category IN a, hostname is b, ', expected: { type: 'field', partial: '' } },
  // Strict field fragment: a list VALUE that happens to spell an operator word
  // (`all`) stays a value of the earlier list — it is not misread as a new
  // operator (which the loose `\S+` fragment did, keying the dropdown on `a,`).
  { name: 'value spelling an operator word stays in the list', query: 'category IN a, all servers, ', expected: { type: 'value', field: 'category', partial: '', listValue: true } },
  // A symbol INSIDE a value (`env=prod` tag, `C$` share) is not a `<field> <op>`
  // boundary, so it must NOT abort chaining — the list stays open for more values.
  { name: 'value containing = still chains (not a filter boundary)', query: 'category IN env=prod, ', expected: { type: 'value', field: 'category', partial: '', listValue: true } },
  { name: 'value containing $ still chains (not a filter boundary)', query: 'category IN c$, ', expected: { type: 'value', field: 'category', partial: '', listValue: true } },
  { name: 'field + operator (trailing space, no value) -> value', query: 'category = ', expected: { type: 'value', field: 'category', partial: '', replaceStart: 11, replaceEnd: 11 } },
  { name: 'quoted field + symbol operator -> value', query: '"OS Normalized" ^ ', expected: { type: 'value', field: 'os normalized', partial: '', replaceStart: 18, replaceEnd: 18 } },
  { name: 'quoted field + empty operator completes filter -> field', query: '"Data Source" empty ', expected: { type: 'field', partial: '', replaceStart: 20, replaceEnd: 20 } },
  { name: 'new partial field after a quoted-field standard filter', query: '"Data Source" = x ho', expected: { type: 'field', partial: 'ho', replaceStart: 18, replaceEnd: 20 } },
  { name: 'field then stray token -> operator', query: 'hostname foo ', expected: { type: 'operator', field: 'hostname', partial: '', replaceStart: 13, replaceEnd: 13 } },
  { name: 'quoted empty operator then partial new field', query: '"Data Source" empty cat', expected: { type: 'field', partial: 'cat', replaceStart: 20, replaceEnd: 23 } },
  // FIX (Version B): cursor parked right after a closing quote, no trailing
  // space. Must behave like the unquoted "still typing" case — field mode with
  // the clean field name as the partial and the range spanning the whole
  // quoted token (was: partial 'source"', range 6-13).
  { name: 'cursor right after closing quote -> field with clean field name', query: '"Data Source"', cursor: 13, expected: { type: 'field', partial: 'data source', replaceStart: 0, replaceEnd: 13 } },
];

describe('detectContext', () => {
  it.each(CASES)('$name', ({ query, cursor, expected }) => {
    const ctx = detectContext(query, cursor ?? query.length, null, fieldKeys);
    expect(ctx).toEqual(expect.objectContaining(expected));
  });
});

const STRING_FIELD: FieldCatalogEntry = {
  display_name: 'Category', field_id: { kind: 'static', name: 'Category' },
  encoded_key: 'static:Category', category: 'target', data_type: 'string',
  is_array: false, is_grouped: false, member_sources: null,
  operator_specs: [
    { code: 'eq', label: 'equals', symbol: '=', aliases: ['=', 'equals'], arity: 'one', value_type: 'text' },
    { code: 'contains', label: 'contains', symbol: '~', aliases: ['~'], arity: 'one', value_type: 'text' },
    { code: 'any', label: 'any of', symbol: 'IN', aliases: ['in'], arity: 'list', value_type: 'text' },
  ],
};

describe('buildOperatorSuggestionsForField', () => {
  it('suggests only the field-valid operators', () => {
    const items = buildOperatorSuggestionsForField('', STRING_FIELD);
    const symbols = items.map((i) => i.value);
    expect(symbols).toContain('=');
    expect(symbols).toContain('IN');
    // No phantom ALL / > on a text field.
    expect(symbols).not.toContain('ALL');
    expect(symbols).not.toContain('>');
  });

  it('filters by partial', () => {
    const items = buildOperatorSuggestionsForField('cont', STRING_FIELD);
    expect(items.map((i) => i.value)).toEqual(['~']);
  });

  it('falls back to legacy table when field has no operator_specs', () => {
    const legacy = { ...STRING_FIELD, operator_specs: undefined as unknown as [] };
    const items = buildOperatorSuggestionsForField('', legacy);
    expect(items.length).toBeGreaterThan(0);
  });
});
