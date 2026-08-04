// SPDX-License-Identifier: AGPL-3.0-or-later
// Explorer query parser — pure TypeScript, zero React/DOM dependencies.
// Extracted from legacy explorer-filters.js.

import type { OperatorSpec } from '@/types/field-id';
import { tokenizeQuery } from './explorer-tokenizer';

export interface ParsedFilter {
  field: string;
  op: string;
  value: string | string[] | null;
}

export interface ParseResult {
  filters: ParsedFilter[];
  keyword: string;
}

// ---------------------------------------------------------------------------
// Operator definitions
// ---------------------------------------------------------------------------

// Canonical Phase-1 operator specs (mirror src/modules/fields/operators.py).
// The live /fields payload supplies per-field operator_specs; this static
// table is the parser's default vocabulary so it works without a payload and
// keeps SearchBar.test.ts green. `aliases` lists every typeable spelling.
export const OPERATOR_SPECS: OperatorSpec[] = [
  { code: 'eq',        label: 'equals',          symbol: '=',      aliases: ['=', '==', 'is', 'equals'],                     arity: 'one',  value_type: 'text' },
  { code: 'neq',       label: 'not equals',      symbol: '!=',     aliases: ['!=', '<>', 'ne', 'not'],                       arity: 'one',  value_type: 'text' },
  { code: 'gt',        label: 'greater than',    symbol: '>',      aliases: ['>', 'gt'],                                     arity: 'one',  value_type: 'number' },
  { code: 'gte',       label: 'greater or equal', symbol: '>=',    aliases: ['>=', 'gte'],                                   arity: 'one',  value_type: 'number' },
  { code: 'lt',        label: 'less than',       symbol: '<',      aliases: ['<', 'lt'],                                     arity: 'one',  value_type: 'number' },
  { code: 'lte',       label: 'less or equal',   symbol: '<=',     aliases: ['<=', 'lte'],                                   arity: 'one',  value_type: 'number' },
  { code: 'contains',  label: 'contains',        symbol: '~',      aliases: ['~', '*', 'like', 'has', 'contains'],           arity: 'one',  value_type: 'text' },
  { code: 'ncontains', label: 'not contains',    symbol: '!~',     aliases: ['!~'],                                          arity: 'one',  value_type: 'text' },
  { code: 'starts',    label: 'starts with',     symbol: '^',      aliases: ['^', 'startswith', 'starts'],                   arity: 'one',  value_type: 'text' },
  { code: 'ends',      label: 'ends with',       symbol: '$',      aliases: ['$', 'endswith', 'ends'],                       arity: 'one',  value_type: 'text' },
  { code: 'any',       label: 'any of',          symbol: 'IN',     aliases: ['in', 'any', 'oneof'],                          arity: 'list', value_type: 'text' },
  { code: 'all',       label: 'all of',          symbol: 'ALL',    aliases: ['all', 'every'],                                arity: 'list', value_type: 'text' },
  { code: 'none',      label: 'none of',         symbol: 'NONE',   aliases: ['none', 'without'],                             arity: 'list', value_type: 'text' },
  { code: 'between',     label: 'between',     symbol: 'BETWEEN', aliases: ['between', 'btw'],                       arity: 'list', value_type: 'number' },
  { code: 'within_last', label: 'in the last', symbol: 'WITHIN',  aliases: ['within_last', 'withinlast', 'within'],  arity: 'one',  value_type: 'date' },
  { code: 'empty',     label: 'is empty',        symbol: 'EMPTY',  aliases: ['empty', 'isempty', 'null', 'blank'],           arity: 'none', value_type: 'text' },
  { code: 'notempty',  label: 'is not empty',    symbol: '!EMPTY', aliases: ['!empty', 'notempty', 'isnotempty', 'notnull', 'exists'], arity: 'none', value_type: 'text' },
];

// alias (lower) -> canonical code.
export const OPERATOR_ALIASES: Record<string, string> = (() => {
  const m: Record<string, string> = {};
  for (const s of OPERATOR_SPECS) {
    m[s.code] = s.code;
    for (const a of s.aliases) m[a.toLowerCase()] = s.code;
  }
  return m;
})();

// canonical code -> symbol (display/insert).
export const OPERATOR_SYMBOLS: Record<string, string> = Object.fromEntries(
  OPERATOR_SPECS.map((s) => [s.code, s.symbol ?? s.code]),
);

// Back-compat: canonical code -> [code, ...aliases]. SearchBar (and the
// passes below) still iterate OPERATORS to build alternation regexes.
export const OPERATORS: Record<string, string[]> = Object.fromEntries(
  OPERATOR_SPECS.map((s) => [s.code, [s.code, ...s.aliases]]),
);

// Canonical codes for operators that take no value (arity 'none').
const VALUELESS_OPERATOR_CODES = new Set(
  OPERATOR_SPECS.filter((s) => s.arity === 'none').map((s) => s.code),
);

/**
 * True when `op` — a canonical code, alias, or inserted symbol (e.g. 'EMPTY',
 * '!EMPTY') — is a valueless operator. Resolves through the same alias table the
 * parser uses, so a positive result guarantees parseSearchQuery will read it as
 * a complete, value-less filter.
 */
export function isValuelessOperator(op: string): boolean {
  const code = OPERATOR_ALIASES[op.toLowerCase()];
  return code != null && VALUELESS_OPERATOR_CODES.has(code);
}

// Canonical codes for operators that take a comma-separated list of values
// (arity 'list': any/all/none/between). Mirror of VALUELESS_OPERATOR_CODES.
export const LIST_OPERATOR_CODES = new Set(
  OPERATOR_SPECS.filter((s) => s.arity === 'list').map((s) => s.code),
);

/**
 * Canonical code if `op` — a code, alias, or inserted symbol — is a list
 * operator (any/all/none/between), else null. Resolves through the same alias
 * table the parser uses, mirroring isValuelessOperator.
 */
export function listOperatorCode(op: string): string | null {
  const code = OPERATOR_ALIASES[op.toLowerCase()];
  return code != null && LIST_OPERATOR_CODES.has(code) ? code : null;
}

/** Strip a single wrapping pair of double or single quotes from a token. */
export function stripQuotes(v: string): string {
  return v.replace(/^"([^"]*)"$/, '$1').replace(/^'([^']*)'$/, '$1');
}

// ---------------------------------------------------------------------------
// Field name resolution
// ---------------------------------------------------------------------------

// Static fallback field mappings (mirrors legacy fieldMappings constant).
const STATIC_FIELD_MAPPINGS: Record<string, string> = {
  hostname:      'hostname',
  ip:            'ip',
  category:      'category',
  os:            'os_normalized',
  os_normalized: 'os_normalized',
  status:        'status',
  last_seen:     'last_seen',
  'last seen':   'last_seen',
  lastseen:      'last_seen',
  first_seen:    'first_seen',
  'first seen':  'first_seen',
  mac:           'mac',
  serial:        'sn',
  sn:            'sn',
  model:         'model',
  manufacturer:  'manufacturer',
  location:      'location',
  owner:         'owner',
  fqdn:          'fqdn',
  subcategory:   'subcategory',
  confidence:    'confidence',
};

/**
 * Resolve a raw field name (or alias) to the canonical field key.
 *
 * @param input - The raw field name to resolve (any casing).
 * @param synonymLookup - Optional API synonym map: canonical_field -> [alias, alias, ...]
 *                        The map is inverted for lookup (alias -> canonical_field).
 * @returns The canonical field key, or null if unresolved.
 */
export function resolveFieldName(
  input: string,
  synonymLookup?: Record<string, string | string[]>,
): string | null {
  if (!input) return null;
  const normalized = input.toLowerCase().trim();

  // API synonyms — supports two shapes:
  // 1. { "alias": "field_name" } (legacy format from /search/synonyms → synonyms sub-object)
  // 2. { "field": ["alias1", "alias2"] } (inverted format)
  if (synonymLookup) {
    for (const [key, val] of Object.entries(synonymLookup)) {
      if (typeof val === 'string') {
        // Shape 1: key is alias, val is field name
        if (key.toLowerCase() === normalized) return val;
      } else if (Array.isArray(val)) {
        // Shape 2: key is field, val is alias array
        if (key.toLowerCase() === normalized) return key;
        if (val.some((a: string) => a.toLowerCase() === normalized)) return key;
      }
    }
  }

  // Static fallback
  if (STATIC_FIELD_MAPPINGS[normalized]) {
    return STATIC_FIELD_MAPPINGS[normalized];
  }

  return null;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Quoted spans as `[start, end)`, from the same tokenizer the rest of the
 *  parser uses — so "inside a quote" means exactly what it means there.
 *  `start` is the opening quote; an unterminated quote runs to end-of-input. */
function quotedSpans(s: string): Array<[number, number]> {
  return tokenizeQuery(s)
    .filter((t) => t.quoted)
    .map((t) => [t.start, t.end] as [number, number]);
}

/**
 * True when a match beginning at `index` starts INSIDE a quoted value.
 *
 * The parser's invariant: no pass may start a filter inside a quoted span.
 * Without this, an operator word in a free-text value started a filter of its
 * own — `Model = "OptiPlex 7070 All in One"` matched field `all` op `any` and
 * left the real filter as `model = "OptiPlex`.
 *
 * Strict on BOTH sides on purpose: a quoted FIELD name (`"OS Normalized" = x`)
 * starts AT the opening quote, so `index > start` keeps it parsing.
 */
function startsInsideQuotes(spans: Array<[number, number]>, index: number): boolean {
  return spans.some(([start, end]) => index > start && index < end);
}

/** Build a sorted (longest first) joined alternation string for a set of aliases. */
function buildAltRegex(aliases: string[]): string {
  return aliases
    .map(escapeRegex)
    .sort((a, b) => b.length - a.length)
    .join('|');
}

/** All operator alternatives flattened into one sorted alternation string. */
function buildAllOpRegex(): string {
  const all: string[] = [];
  for (const alts of Object.values(OPERATORS)) {
    all.push(...alts);
  }
  return buildAltRegex(all);
}

/** Map a raw operator string to a canonical operator key. */
function normalizeOperator(raw: string): string {
  return OPERATOR_ALIASES[raw.toLowerCase()] ?? 'contains'; // safe default
}

// ---------------------------------------------------------------------------
// Main parser
// ---------------------------------------------------------------------------

/**
 * Parse a free-text search query into structured filters and a keyword remainder.
 *
 * Examples:
 *   "category = Server os contains Windows free text"
 *   -> filters: [{field:'category',op:'eq',value:'Server'}, {field:'os_normalized',op:'contains',value:'Windows'}]
 *   -> keyword: 'free text'
 *
 * @param query - The raw search string.
 * @param synonymLookup - Optional API synonym map passed through to resolveFieldName.
 */
export function parseSearchQuery(
  query: string,
  synonymLookup?: Record<string, string | string[]>,
): ParseResult {
  const filters: ParsedFilter[] = [];

  if (!query || !query.trim()) {
    return { filters, keyword: '' };
  }

  let remaining = query;

  // Field pattern fragment: "quoted field", 'quoted field', or identifier.
  // Unquoted form admits `:` and `%` so encoded FieldIds (`static:Location`,
  // `static:OS%20Normalized`, `ext:Intune:deviceName`) parse as a single token.
  const FIELD_FRAG = `(?:"([^"]+)"|'([^']+)'|([a-zA-Z_][a-zA-Z0-9_]*(?::[a-zA-Z0-9_%]+)*))`;

  // --- Pass 1: empty / notempty operators (no value required) ---

  const emptyAltRegex = buildAltRegex([
    ...OPERATORS.empty,
    ...OPERATORS.notempty,
  ]);

  const emptyPattern = new RegExp(
    `${FIELD_FRAG}\\s+(${emptyAltRegex})(?=\\s|$)`,
    'gi',
  );

  let m: RegExpExecArray | null;
  while ((m = emptyPattern.exec(remaining)) !== null) {
    if (startsInsideQuotes(quotedSpans(remaining), m.index)) continue;
    const rawFieldOriginal1 = (m[1] ?? m[2] ?? m[3] ?? '').trim();
    const rawFieldLower1 = rawFieldOriginal1.toLowerCase();
    const rawOp = m[4].trim().toLowerCase();
    const field = rawFieldOriginal1.includes('__')
      ? rawFieldOriginal1
      : (resolveFieldName(rawFieldLower1, synonymLookup) ?? rawFieldLower1);
    const op = OPERATORS.notempty.some((a) => a.toLowerCase() === rawOp)
      ? 'notempty'
      : 'empty';
    filters.push({ field, op, value: null });
    remaining =
      remaining.substring(0, m.index) +
      ' ' +
      remaining.substring(m.index + m[0].length);
    emptyPattern.lastIndex = 0;
  }

  // --- Pass 2: any / all operators (multi-word values) ---

  const anyAllAltRegex = buildAltRegex([
    ...OPERATORS.any,
    ...OPERATORS.all,
    ...OPERATORS.none,
    ...OPERATORS.between,
  ]);
  const allOpRegex = buildAllOpRegex();

  // \s+ (not \s*) before the operator: any/all/none/between are alphabetic, so a
  // word boundary is required or the alias would match inside a word (C-1).
  const anyAllPattern = new RegExp(
    `${FIELD_FRAG}\\s+(${anyAllAltRegex})\\s+(.+?)` +
      `(?=\\s+(?:"[^"]+"|'[^']+'|[a-zA-Z_][a-zA-Z0-9_]*)\\s*(?:${allOpRegex})(?:\\s|$)|$)`,
    'gi',
  );

  while ((m = anyAllPattern.exec(remaining)) !== null) {
    if (startsInsideQuotes(quotedSpans(remaining), m.index)) continue;
    const rawFieldOriginal2 = (m[1] ?? m[2] ?? m[3] ?? '').trim();
    const rawFieldLower2 = rawFieldOriginal2.toLowerCase();
    const rawOp = m[4].trim().toLowerCase();
    const rawValue = (m[5] ?? '').trim();
    const field = rawFieldOriginal2.includes('__')
      ? rawFieldOriginal2
      : (resolveFieldName(rawFieldLower2, synonymLookup) ?? rawFieldLower2);
    const op = normalizeOperator(rawOp); // 'any' | 'all' | 'none'
    // Quote-aware value split (limitations §15): tokenizeQuery keeps quoted
    // multi-word spans whole ("Network Device" -> one value), while commas
    // (which the tokenizer treats as literal characters) still split unquoted
    // tokens, so a plain `a,b` list is preserved. A quote abutting a comma with
    // no space (e.g. "a","b") isn't seen as a span boundary, so strip a leftover
    // wrapping quote pair from each split value (module-level stripQuotes).
    const value = tokenizeQuery(rawValue)
      .flatMap((t) => (t.quoted ? [t.text] : t.text.split(',')))
      .map((v) => stripQuotes(v.trim()))
      .filter((v) => v.length > 0);
    filters.push({ field, op, value });
    remaining =
      remaining.substring(0, m.index) +
      ' ' +
      remaining.substring(m.index + m[0].length);
    anyAllPattern.lastIndex = 0;
  }

  // --- Pass 3: standard operators with a single value ---

  const stdOpAlts: string[] = [];
  for (const [key, alts] of Object.entries(OPERATORS)) {
    if (
      key === 'empty' ||
      key === 'notempty' ||
      key === 'any' ||
      key === 'all' ||
      key === 'none' ||
      key === 'between'
    ) {
      continue;
    }
    stdOpAlts.push(...alts);
  }
  // Symbol operators (=, ~, >=, …) may abut the field (\s*) so `category=Server`
  // still parses; word operators (is/equals/contains/within/…) require \s+ and a
  // trailing word boundary so they cannot match inside a word (C-1).
  const stdSymRegex = buildAltRegex(stdOpAlts.filter((a) => /^[^a-zA-Z0-9]/.test(a)));
  const stdWordRegex = buildAltRegex(stdOpAlts.filter((a) => /^[a-zA-Z0-9]/.test(a)));

  // Value: "quoted (can be empty)", 'quoted (can be empty)', or \S+ (non-empty unquoted word)
  const filterPattern = new RegExp(
    `${FIELD_FRAG}(?:\\s*(${stdSymRegex})|\\s+(${stdWordRegex})\\b)` +
      `\\s+(?:"([^"]*)"|'([^']*)'|(\\S+))`,
    'gi',
  );

  while ((m = filterPattern.exec(remaining)) !== null) {
    if (startsInsideQuotes(quotedSpans(remaining), m.index)) continue;
    const rawFieldOriginal = (m[1] ?? m[2] ?? m[3] ?? '').trim();
    const rawFieldLower = rawFieldOriginal.toLowerCase();
    // m[4] = symbol operator, m[5] = word operator (one of them matches)
    const rawOp = (m[4] ?? m[5] ?? '').trim().toLowerCase();
    // Groups 6, 7, 8 correspond to double-quoted, single-quoted, unquoted value
    const rawValue = (
      m[6] !== undefined ? m[6] : m[7] !== undefined ? m[7] : (m[8] ?? '')
    ).trim();
    // For extended fields (contain __), preserve original case; otherwise resolve/lowercase
    const field = rawFieldOriginal.includes('__')
      ? rawFieldOriginal
      : (resolveFieldName(rawFieldLower, synonymLookup) ?? rawFieldLower);
    const op = normalizeOperator(rawOp);
    filters.push({ field, op, value: rawValue });
    remaining =
      remaining.substring(0, m.index) +
      ' ' +
      remaining.substring(m.index + m[0].length);
    filterPattern.lastIndex = 0;
  }

  // Remainder is the free-text keyword
  const keyword = remaining.replace(/\s+/g, ' ').trim();

  return { filters, keyword };
}
