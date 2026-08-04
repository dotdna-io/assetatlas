// SPDX-License-Identifier: AGPL-3.0-or-later
import {
  useState,
  useRef,
  useCallback,
  useEffect,
  useMemo,
} from 'react';
import type { FieldCatalogEntry } from '@/types/field-id';
import { assetsApi } from '@/api/assets';
import { findFieldEntry, getDisplayName, getEntrySource } from '../explorer-utils';
import { tokenizeQuery } from '../explorer-tokenizer';
import type { ParseResult } from '../explorer-query-parser';
import { OPERATORS, OPERATOR_SYMBOLS, OPERATOR_SPECS, LIST_OPERATOR_CODES, listOperatorCode, stripQuotes, isValuelessOperator, parseSearchQuery, resolveFieldName } from '../explorer-query-parser';
import { SuggestionPanel, groupBySource } from './SuggestionPanel';
import type { SuggestionMode, SuggestionItem } from './SuggestionPanel';
import styles from './search.module.css';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SearchBarProps {
  value: string;
  onChange: (value: string, parsed: ParseResult) => void;
  fields: FieldCatalogEntry[];
  synonymLookup: Record<string, string | string[]> | null;
}

// ---------------------------------------------------------------------------
// Context detection — ported from legacy detectSuggestionContext
// ---------------------------------------------------------------------------

export interface SuggestionContext {
  type: SuggestionMode;
  partial: string;
  field?: string;
  replaceStart: number;
  replaceEnd: number;
  /** On a `value` context, whether picking a value should chain another one:
   *  insertSuggestion appends a comma (keeping the dropdown open) when true, or a
   *  space (completing the filter) when false/absent. True for list operators
   *  with room for more values — IN/ALL/NONE always, BETWEEN only for its first
   *  of two bounds. */
  listValue?: boolean;
}

/** All operator alternative strings, collected once. */
function collectAllOperatorAlts(): string[] {
  const alts: string[] = [];
  for (const aliases of Object.values(OPERATORS)) {
    alts.push(...aliases);
  }
  return alts;
}

const ALL_OP_ALTS = collectAllOperatorAlts();

// Field fragment for the filter-boundary regexes below: a quoted span (single
// or double) or an unquoted run of non-space chars. It only locates where a
// field token ends and its operator begins — the match groups are never read —
// so it stays deliberately loose, independent of the parser's stricter,
// capturing FIELD_FRAG (which extracts the field value).
const FIELD_FRAG = `(?:"[^"]*"|'[^']*'|\\S+)`;

// Upper bound on how many values a list operator accepts (absent = unbounded).
// `between` is a [lo, hi] range, so exactly two — this caps value chaining so it
// completes after the second bound instead of inviting an invalid third.
const LIST_OP_MAX_VALUES: Record<string, number> = { between: 2 };

// Every typeable spelling (code + aliases + inserted symbol) of a list operator,
// lower-cased, feeding the value-continuation regex below.
const LIST_OP_ALTS = new Set(
  OPERATOR_SPECS.filter((s) => LIST_OPERATOR_CODES.has(s.code))
    .flatMap((s) => [s.code, s.symbol ?? s.code, ...s.aliases])
    .map((a) => a.toLowerCase()),
);

// Alternation of those spellings for the value-continuation regex below.
const LIST_OP_REGEX_ALT = Array.from(LIST_OP_ALTS)
  .map(escRx)
  .sort(byLenDesc)
  .join('|');

// Stricter field fragment for the value-continuation regex — mirrors the
// parser's field grammar (quoted span or identifier, NO commas/symbols) rather
// than the loose `\S+` FIELD_FRAG. Using `\S+` here let a list VALUE that
// happens to spell an operator word, or a scalar value, be misread as a
// `field <list-op>` pair (e.g. `owner = all ,` matched field `=` + op `all`).
const STRICT_FIELD_FRAG = `(?:"[^"]+"|'[^']+'|[a-zA-Z_][a-zA-Z0-9_]*(?::[a-zA-Z0-9_%]+)*)`;

// `field <list-op>` at each position in the value region. Static (both
// fragments are module constants) so it is compiled once, not per keystroke.
// Global flag is required by matchAll below; matchAll uses its own iterator and
// does not mutate the regex's lastIndex, so sharing the instance is safe.
const LIST_OP_CONT_PATTERN = new RegExp(
  `(${STRICT_FIELD_FRAG})\\s+(${LIST_OP_REGEX_ALT})(?=\\s)`,
  'gi',
);

// A new `field <operator>` filter starting inside the value region means the
// cursor has moved PAST this list into a later filter — so the list must not
// resume across it. Detected exactly the way parseSearchQuery delimits a list
// value from the next filter (its Pass-2 lookahead): whitespace, a field token,
// then an operator followed by whitespace/end. This is far more precise than
// scanning for an operator *character*: a symbol inside a value (`C$`,
// `env=prod`, a quoted `"a=b"`) is NOT `<field> <op> <ws>`, so it is correctly
// ignored, while both symbol AND word operators (`host = b`, `role is bob`) are
// caught. Mirrors the parser so the two never disagree on where a list ends.
const ALL_OP_REGEX_ALT = ALL_OP_ALTS.map(escRx).sort(byLenDesc).join('|');
const NEW_FILTER_IN_REGION = new RegExp(
  `\\s+${STRICT_FIELD_FRAG}\\s*(?:${ALL_OP_REGEX_ALT})(?:\\s|$)`,
  'i',
);

/**
 * Whether picking/typing a value for `opToken` should chain another value
 * (append a comma and keep the dropdown open), given how many values are already
 * committed. Unbounded list ops (any/all/none) always chain; `between` chains
 * only after its first bound and completes after the second; non-list operators
 * never chain.
 */
function shouldChainValue(opToken: string, committedCount: number): boolean {
  const code = listOperatorCode(opToken);
  if (!code) return false;
  const max = LIST_OP_MAX_VALUES[code] ?? Infinity;
  return committedCount + 1 < max;
}

/**
 * Detect the suggestion context at `cursorPos` within `query`.
 * Returns the mode (field / operator / value / null) and the range to replace.
 *
 * Exported for unit testing.
 */
export function detectContext(
  query: string,
  cursorPos: number,
  synonymLookup: Record<string, string | string[]> | null,
  fieldKeys: Set<string>,
): SuggestionContext {
  const textToCursor = query.substring(0, cursorPos);

  // --- Quoted string detection: don't suggest inside unclosed quotes ---
  let inDouble = false;
  let inSingle = false;
  for (let i = 0; i < cursorPos; i++) {
    if (query[i] === '"' && !inSingle) inDouble = !inDouble;
    else if (query[i] === "'" && !inDouble) inSingle = !inSingle;
  }
  if (inDouble || inSingle) {
    return { type: null, partial: '', replaceStart: cursorPos, replaceEnd: cursorPos };
  }

  // Empty input -> suggest fields
  if (!textToCursor.trim()) {
    return { type: 'field', partial: '', replaceStart: 0, replaceEnd: cursorPos };
  }

  // Find the token at the cursor (quote-aware). A quoted token counts as the
  // current one only when the cursor sits just past its closing quote — the
  // unclosed-quote guard above already bails while the cursor is inside one.
  const tokens = tokenizeQuery(query);
  const current = tokens.find((t) =>
    t.quoted ? cursorPos === t.end : cursorPos >= t.start && cursorPos <= t.end,
  );
  let wordStart: number;
  let wordEnd: number;
  let partialWord: string;
  if (current) {
    wordStart = current.start;
    wordEnd = current.end;
    // For a quoted token the cursor is past the close quote, so the whole inner
    // text is the partial; otherwise it's the text typed up to the cursor.
    partialWord = (
      current.quoted ? current.text : query.substring(current.start, cursorPos)
    ).toLowerCase();
  } else {
    wordStart = cursorPos;
    wordEnd = cursorPos;
    partialWord = '';
  }
  const textBeforeWord = query.substring(0, wordStart).trim();

  // Nothing before this word -> typing a field name
  if (!textBeforeWord) {
    return {
      type: 'field',
      partial: partialWord,
      replaceStart: 0,
      replaceEnd: wordEnd,
    };
  }

  // -- Helper: resolve field name using synonym lookup + known field keys --
  // Tokens arrive already unquoted from tokenizeQuery, so no stripping here.
  const resolveField = (term: string): string | null => {
    const result = resolveFieldName(term, synonymLookup ?? undefined);
    if (result) return result;
    // Also check raw field keys (handles extended fields)
    const lower = term.toLowerCase();
    if (fieldKeys.has(lower)) return lower;
    for (const k of fieldKeys) {
      if (k.toLowerCase() === lower) return k;
    }
    return null;
  };

  // -- List-operator value continuation --
  // When the cursor sits in the value list of a list operator (IN/ALL/NONE/
  // BETWEEN) and the values entered so far end with a comma, the user is adding
  // another value. Keep suggesting values for that field instead of treating the
  // filter as complete (which would prompt for a new field and make lists
  // impossible to build from the dropdown). The trailing comma is the "another
  // value coming" signal insertSuggestion writes after each picked value. A
  // value followed by a space with no comma is still complete — handled below.
  if (/,\s*$/.test(textBeforeWord)) {
    const contMatches = [...textBeforeWord.matchAll(LIST_OP_CONT_PATTERN)];
    const lastOp = contMatches[contMatches.length - 1];
    if (lastOp) {
      // The text after the operator is this list's value region (it ends in the
      // trailing comma the outer test matched). If a new `field <op>` filter
      // starts in it, the cursor has moved into a LATER filter (e.g.
      // `os IN a, host = b,`) — don't resume the earlier list across it.
      const afterOp = textBeforeWord.slice(lastOp.index! + lastOp[0].length);
      if (!NEW_FILTER_IN_REGION.test(afterOp)) {
        const committed = afterOp
          .split(',')
          .map((v) => v.trim())
          .filter((v) => v.length > 0).length;
        const fieldTok = stripQuotes(lastOp[1]);
        return {
          type: 'value',
          field: resolveField(fieldTok) ?? fieldTok.toLowerCase(),
          partial: partialWord,
          replaceStart: wordStart,
          replaceEnd: wordEnd,
          // `between` stops chaining after its second bound; unbounded ops keep going.
          listValue: shouldChainValue(lastOp[2], committed),
        };
      }
    }
  }

  // -- Find where the last complete filter ends --
  // Complete filters: "field op value" or "field empty_op"
  const emptyAlts = [...OPERATORS.empty, ...OPERATORS.notempty];
  const emptyPattern = new RegExp(
    `${FIELD_FRAG}\\s+(${emptyAlts.map(escRx).sort(byLenDesc).join('|')})(?=\\s|$)`,
    'gi',
  );
  let lastFilterEnd = 0;
  let m: RegExpExecArray | null;
  while ((m = emptyPattern.exec(textBeforeWord)) !== null) {
    lastFilterEnd = Math.max(lastFilterEnd, m.index + m[0].length);
  }

  // Standard complete filter: field op value (quoted or unquoted). Exclude the
  // valueless operators (empty/notempty) from this "op + value" alternation —
  // they never take a value, so the field token starting the *next* condition
  // (e.g. `criticality EMPTY category`) must not be swallowed as a bogus value.
  // The emptyPattern above already accounts for valueless filters; counting one
  // here too would mark the next field as consumed and re-prompt for a field,
  // duplicating it when the user picks the still-shown suggestion.
  const emptyAltSet = new Set(emptyAlts.map((a) => a.toLowerCase()));
  const valueOpAlts = ALL_OP_ALTS.filter((a) => !emptyAltSet.has(a.toLowerCase()));
  const allOpRegex = valueOpAlts.map(escRx).sort(byLenDesc).join('|');
  const completePattern = new RegExp(
    `${FIELD_FRAG}\\s+(${allOpRegex})\\s+(?:"[^"]+"|'[^']+'|\\S+)`,
    'gi',
  );
  while ((m = completePattern.exec(textBeforeWord)) !== null) {
    lastFilterEnd = Math.max(lastFilterEnd, m.index + m[0].length);
  }

  const textAfterFilters = textBeforeWord.substring(lastFilterEnd).trim();

  if (textAfterFilters) {
    const wordsAfter = tokenizeQuery(textAfterFilters).map((t) => t.text);

    // Try multi-word alias detection (up to 3 words)
    for (let numWords = Math.min(wordsAfter.length, 3); numWords >= 1; numWords--) {
      const phrase = wordsAfter.slice(0, numWords).join(' ');
      const resolved = resolveField(phrase);
      if (resolved) {
        const remainingWords = wordsAfter.slice(numWords);
        if (remainingWords.length > 0) {
          const nextWord = remainingWords[0].toLowerCase();
          if (ALL_OP_ALTS.some((op) => op.toLowerCase() === nextWord)) {
            // field + operator found -> need value
            return {
              type: 'value',
              field: resolved,
              partial: partialWord,
              replaceStart: wordStart,
              replaceEnd: wordEnd,
              listValue: shouldChainValue(nextWord, 0),
            };
          }
        }
        // field found but no operator -> suggest operators
        return {
          type: 'operator',
          field: resolved,
          partial: partialWord,
          replaceStart: wordStart,
          replaceEnd: wordEnd,
        };
      }
    }

    // Single-word field check
    const potentialField = wordsAfter[0];
    const resolvedSingle = resolveField(potentialField);
    const hasUnderscore = potentialField.includes('_');

    if (resolvedSingle || hasUnderscore) {
      const fieldName = resolvedSingle || potentialField.toLowerCase();
      if (wordsAfter.length > 1) {
        const nextWord = wordsAfter[1].toLowerCase();
        if (ALL_OP_ALTS.some((op) => op.toLowerCase() === nextWord)) {
          return {
            type: 'value',
            field: fieldName,
            partial: partialWord,
            replaceStart: wordStart,
            replaceEnd: wordEnd,
            listValue: shouldChainValue(nextWord, 0),
          };
        }
      }
      return {
        type: 'operator',
        field: fieldName,
        partial: partialWord,
        replaceStart: wordStart,
        replaceEnd: wordEnd,
      };
    }
  } else if (lastFilterEnd > 0) {
    // After a complete filter with nothing remaining -> new field
    return {
      type: 'field',
      partial: partialWord,
      replaceStart: wordStart,
      replaceEnd: wordEnd,
    };
  }

  // Check: "field operator " pattern at end of textBeforeWord -> typing value
  for (const [opKey, alts] of Object.entries(OPERATORS)) {
    if (opKey === 'empty' || opKey === 'notempty') continue;
    for (const alt of alts) {
      const escaped = escRx(alt);
      const fieldOpPattern = new RegExp(`(\\S+)\\s+${escaped}\\s*$`, 'i');
      const match = textBeforeWord.match(fieldOpPattern);
      if (match) {
        const rawField = match[1].toLowerCase();
        const resolvedF = resolveField(rawField) || rawField;
        return {
          type: 'value',
          field: resolvedF,
          partial: partialWord,
          replaceStart: wordStart,
          replaceEnd: wordEnd,
          listValue: shouldChainValue(opKey, 0),
        };
      }
    }
  }

  // Check: after empty/notempty -> new field
  for (const alt of [...OPERATORS.empty, ...OPERATORS.notempty]) {
    const escaped = escRx(alt);
    const pattern = new RegExp(`\\S+\\s+${escaped}\\s*$`, 'i');
    if (pattern.test(textBeforeWord) || pattern.test(textToCursor)) {
      return {
        type: 'field',
        partial: partialWord,
        replaceStart: wordStart,
        replaceEnd: wordEnd,
      };
    }
  }

  // Check: text before is a known field -> operator
  const wordsBefore = tokenizeQuery(textBeforeWord).map((t) => t.text);
  const lastWordBefore = wordsBefore[wordsBefore.length - 1] || '';
  const resolvedLast = resolveField(lastWordBefore);
  if (resolvedLast) {
    return {
      type: 'operator',
      field: resolvedLast,
      partial: partialWord,
      replaceStart: wordStart,
      replaceEnd: wordEnd,
    };
  }

  // Fallback: field identifier (snake_case)
  if (/^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/i.test(lastWordBefore)) {
    return {
      type: 'operator',
      field: lastWordBefore.toLowerCase(),
      partial: partialWord,
      replaceStart: wordStart,
      replaceEnd: wordEnd,
    };
  }

  // Multi-word check for phrases before current word
  for (let i = Math.max(0, wordsBefore.length - 3); i < wordsBefore.length; i++) {
    const phrase = wordsBefore.slice(i).join(' ').toLowerCase();
    const resolved = resolveField(phrase);
    if (resolved) {
      return {
        type: 'operator',
        field: resolved,
        partial: partialWord,
        replaceStart: wordStart,
        replaceEnd: wordEnd,
      };
    }
  }

  // Default: typing a field name
  return {
    type: 'field',
    partial: partialWord,
    replaceStart: wordStart,
    replaceEnd: wordEnd,
  };
}

// ---------------------------------------------------------------------------
// Field suggestion building — ported from legacy buildFieldSuggestions
// ---------------------------------------------------------------------------

function buildFieldSuggestions(
  partial: string,
  fields: FieldCatalogEntry[],
  synonymLookup: Record<string, string | string[]> | null,
): SuggestionItem[] {
  if (fields.length === 0) return [];

  const query = partial.toLowerCase();
  const byField = new Map<string, SuggestionItem>();

  // 1. Match against encoded_key, display name, and source
  for (const field of fields) {
    const key = field.encoded_key.toLowerCase();
    const name = (field.display_name || '').toLowerCase();
    const source = getEntrySource(field);
    let score = 0;

    // Key match
    if (key === query) score = 1.0;
    else if (key.startsWith(query)) score = 0.9;
    else if (key.includes(query)) score = 0.7;

    // Display name match
    if (name === query) score = Math.max(score, 0.95);
    else if (name.startsWith(query)) score = Math.max(score, 0.85);
    else if (name.includes(query)) score = Math.max(score, 0.65);

    // Source match (extended_attribute / integration_group entries only)
    if (source && source.toLowerCase().includes(query)) {
      score = Math.max(score, 0.6);
    }

    if (score > 0) {
      const existing = byField.get(field.encoded_key);
      if (!existing || (existing.score ?? 0) < score) {
        byField.set(field.encoded_key, {
          text: getDisplayName(field) || field.encoded_key,
          value: field.encoded_key,
          source: source ?? 'AssetAtlas',
          score,
        });
      }
    }
  }

  // 2. Synonym matches — legacy short names ("hostname", "ip", ...) mapped to
  // FieldCatalogEntry via display_name match when possible.
  if (synonymLookup && query.length >= 2) {
    for (const [key, val] of Object.entries(synonymLookup)) {
      const fieldName = typeof val === 'string' ? val : key;
      const aliasArr = typeof val === 'string' ? [key] : (Array.isArray(val) ? val : []);

      let score = 0;
      if (fieldName.toLowerCase().includes(query)) {
        score = fieldName.toLowerCase() === query ? 1.0 : 0.7;
      }
      for (const alias of aliasArr) {
        const a = alias.toLowerCase();
        if (a.includes(query) || query.includes(a)) {
          const s = a === query ? 1.0 : a.startsWith(query) ? 0.9 : 0.7;
          score = Math.max(score, s);
        }
      }
      if (score > 0) {
        const fieldMeta = fields.find(
          (f) =>
            f.display_name.toLowerCase() === fieldName.toLowerCase() ||
            f.encoded_key === fieldName,
        );
        if (!fieldMeta) continue;
        const existing = byField.get(fieldMeta.encoded_key);
        if (!existing || (existing.score ?? 0) < score) {
          byField.set(fieldMeta.encoded_key, {
            text: getDisplayName(fieldMeta),
            value: fieldMeta.encoded_key,
            source: getEntrySource(fieldMeta) ?? 'AssetAtlas',
            score,
          });
        }
      }
    }
  }

  const results = Array.from(byField.values());
  results.sort((a, b) => (b.score ?? 0) - (a.score ?? 0) || a.value.localeCompare(b.value));
  return results.slice(0, 20);
}

// ---------------------------------------------------------------------------
// Operator suggestions
// ---------------------------------------------------------------------------

/**
 * Type-aware operator suggestions. When the resolved field carries
 * `operator_specs` (live /fields payload), suggest only those operators —
 * resolved labels + symbols — so phantom ops (`ALL` on text, `>`/`<` on
 * strings) never appear. Falls back to the field-agnostic legacy table when a
 * field has no specs (e.g. catalog not yet loaded).
 */
export function buildOperatorSuggestionsForField(
  partial: string,
  field: FieldCatalogEntry | undefined,
): SuggestionItem[] {
  const q = partial.toLowerCase();
  const specs = field?.operator_specs;
  if (specs && specs.length > 0) {
    const out: SuggestionItem[] = [];
    for (const s of specs) {
      const symbol = s.symbol ?? s.code;
      const label = `${symbol} ${s.label}`;
      if (
        !q ||
        s.code.includes(q) ||
        label.toLowerCase().includes(q) ||
        s.aliases.some((a) => a.toLowerCase().includes(q))
      ) {
        out.push({ text: label, value: symbol });
      }
    }
    return out;
  }
  // Legacy fallback: derive from the static OPERATOR_SPECS-backed tables.
  return buildOperatorSuggestions(partial);
}

// Labels derived once (at module load) from the parser's canonical specs so
// the deleted triplet stays gone. Used by the legacy fallback suggester below.
const OPERATOR_LABELS: Record<string, string> = Object.fromEntries(
  OPERATOR_SPECS.map((s) => [s.code, `${s.symbol ?? s.code} ${s.label}`]),
);

function buildOperatorSuggestions(partial: string): SuggestionItem[] {
  const results: SuggestionItem[] = [];
  const q = partial.toLowerCase();

  for (const [key, alts] of Object.entries(OPERATORS)) {
    // Use the canonical symbol for insertion, not the alias key name
    const symbol = OPERATOR_SYMBOLS[key] ?? alts[0];
    const label = OPERATOR_LABELS[key] ?? symbol;

    if (!q) {
      results.push({ text: label, value: symbol });
      continue;
    }

    // Filter by partial
    const matches =
      key.includes(q) ||
      label.toLowerCase().includes(q) ||
      alts.some((a) => a.toLowerCase().includes(q));

    if (matches) {
      results.push({ text: label, value: symbol });
    }
  }

  return results;
}

// ---------------------------------------------------------------------------
// Regex helpers
// ---------------------------------------------------------------------------

function escRx(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function byLenDesc(a: string, b: string): number {
  return b.length - a.length;
}

// ---------------------------------------------------------------------------
// Default field suggestions (empty input)
// ---------------------------------------------------------------------------

// Default display-names shown when the user opens the search bar with no
// input. These match FieldCatalogEntry.display_name for quick lookup.
const PREFERRED_DEFAULTS = ['Category', 'Hostname', 'IP Address', 'OS Normalized', 'Manufacturer'];

function buildDefaultSuggestions(fields: FieldCatalogEntry[]): SuggestionItem[] {
  if (fields.length === 0) return [];
  const items: SuggestionItem[] = [];
  for (const name of PREFERRED_DEFAULTS) {
    const f = fields.find((fld) => fld.display_name === name);
    if (f) {
      items.push({
        text: getDisplayName(f),
        value: f.encoded_key,
        source: getEntrySource(f) ?? 'AssetAtlas',
        score: 1.0 - items.length * 0.01,
      });
    }
  }
  return items;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SearchBar({ value, onChange, fields, synonymLookup }: SearchBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [localValue, setLocalValue] = useState(value);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [suggestionMode, setSuggestionMode] = useState<SuggestionMode>(null);
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [contextRef, setContextRef] = useState<SuggestionContext | null>(null);

  // Debounce timer for onChange callback
  const onChangeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce timer for semantic suggestions
  const semanticTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Sequence counter for value suggestion requests
  const valueSuggestionSeqRef = useRef(0);

  // Cache for semantic field suggestions
  const semanticCacheRef = useRef<Map<string, SuggestionItem[]>>(new Map());

  // Track the latest semantic partial for stale-response guard
  const latestSemanticPartialRef = useRef<string>('');

  // Sync external value changes
  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  // Build a set of known field keys for context detection. Include both
  // the encoded canonical form and the lowercased display name so the
  // parser can match user-typed names like "category" or "os normalized"
  // as well as encoded forms emitted by suggestion insertion.
  const fieldKeySet = useMemo(() => {
    const set = new Set<string>();
    for (const f of fields) {
      set.add(f.encoded_key);
      set.add(f.display_name.toLowerCase());
    }
    return set;
  }, [fields]);

  // Memoize default field suggestions
  const defaultSuggestions = useMemo(() => buildDefaultSuggestions(fields), [fields]);

  // -------------------------------------------------------------------
  // Fetch value suggestions from API
  // -------------------------------------------------------------------
  const fetchValueSuggestions = useCallback(
    (field: string, partial: string) => {
      // /api/assets/values requires an encoded FieldId — resolve any token.
      const entry = findFieldEntry(field, fields);
      if (!entry) {
        setShowSuggestions(false);
        return;
      }
      const seq = ++valueSuggestionSeqRef.current;

      assetsApi
        .getFieldValues({ field: entry.encoded_key, limit: 30, search: partial || undefined })
        .then((data) => {
          if (seq !== valueSuggestionSeqRef.current) return; // superseded
          const values = (data.values || []).map(
            (v): SuggestionItem => ({
              text: String(v.value),
              value: String(v.value),
              count: v.count,
            }),
          );
          if (values.length === 0) {
            setShowSuggestions(false);
            return;
          }
          setSuggestionMode('value');
          setSuggestions(values);
          setSelectedIndex(-1);
          setShowSuggestions(true);
        })
        .catch(() => {
          if (seq !== valueSuggestionSeqRef.current) return;
          // Silently hide on error
          setShowSuggestions(false);
        });
    },
    [fields],
  );

  // -------------------------------------------------------------------
  // Fetch semantic field suggestions (debounced)
  // -------------------------------------------------------------------
  const fetchSemanticSuggestions = useCallback(
    (partial: string, localItems: SuggestionItem[]) => {
      if (semanticTimerRef.current) clearTimeout(semanticTimerRef.current);

      const cacheKey = partial.toLowerCase().trim();
      latestSemanticPartialRef.current = cacheKey;

      // Check cache
      const cached = semanticCacheRef.current.get(cacheKey);
      if (cached) {
        const merged = mergeSuggestions(localItems, cached);
        setSuggestions(groupBySource(merged).flatMap((g) => g.items));
        return;
      }

      semanticTimerRef.current = setTimeout(() => {
        assetsApi
          .getSemanticSuggestions(partial, 20, 0.5)
          .then((response) => {
            // Stale guard: verify the partial still matches what the user is typing
            if (latestSemanticPartialRef.current !== cacheKey) return;

            // The response may be wrapped; handle both array and object shapes
            const raw = Array.isArray(response)
              ? response
              : (response as any)?.suggestions ?? [];
            if (!raw || raw.length === 0) return;

            const apiItems: SuggestionItem[] = raw
              .filter((s: any) => fieldKeySet.has(s.field_name ?? s.field))
              .map((s: any): SuggestionItem => {
                const key = s.field_name ?? s.field;
                const meta = fields.find(
                  (f) =>
                    f.encoded_key === key ||
                    f.display_name.toLowerCase() === String(key).toLowerCase(),
                );
                return {
                  text: meta ? getDisplayName(meta) : key,
                  value: meta?.encoded_key ?? key,
                  source: (meta ? getEntrySource(meta) : null) ?? 'AssetAtlas',
                  score: s.score ?? 0.5,
                };
              });

            const merged = mergeSuggestions(localItems, apiItems);
            semanticCacheRef.current.set(cacheKey, apiItems);
            setSuggestions(groupBySource(merged).flatMap((g) => g.items));
          })
          .catch(() => {
            // API failed, local suggestions already displayed
          });
      }, 500);
    },
    [fields, fieldKeySet],
  );

  // -------------------------------------------------------------------
  // Core: update suggestions based on input
  // -------------------------------------------------------------------
  const updateSuggestions = useCallback(
    (inputValue: string, cursor: number) => {
      const ctx = detectContext(inputValue, cursor, synonymLookup, fieldKeySet);
      setContextRef(ctx);

      if (ctx.type === null) {
        setShowSuggestions(false);
        setSuggestionMode(null);
        setSuggestions([]);
        return;
      }

      if (ctx.type === 'field') {
        let items: SuggestionItem[];
        if (ctx.partial.length === 0) {
          items = defaultSuggestions;
        } else {
          items = buildFieldSuggestions(ctx.partial, fields, synonymLookup);
        }

        if (items.length === 0 && ctx.partial.length === 0) {
          setShowSuggestions(false);
          return;
        }

        // Sort items in the same grouped order the panel renders them
        // so selectedIndex matches the visual order
        const ordered = groupBySource(items).flatMap((g) => g.items);

        setSuggestionMode('field');
        setSuggestions(ordered);
        setSelectedIndex(-1);
        setShowSuggestions(true);

        // Also fetch semantic suggestions for richer results (2+ chars)
        if (ctx.partial.length >= 2) {
          fetchSemanticSuggestions(ctx.partial, items);
        }
        return;
      }

      if (ctx.type === 'operator') {
        const fieldEntry = ctx.field ? findFieldEntry(ctx.field, fields) ?? undefined : undefined;
        const items = buildOperatorSuggestionsForField(ctx.partial, fieldEntry);
        if (items.length === 0) {
          setShowSuggestions(false);
          return;
        }
        setSuggestionMode('operator');
        setSuggestions(items);
        setSelectedIndex(-1);
        setShowSuggestions(true);
        return;
      }

      if (ctx.type === 'value' && ctx.field) {
        fetchValueSuggestions(ctx.field, ctx.partial);
        return;
      }

      setShowSuggestions(false);
    },
    [
      fields,
      fieldKeySet,
      synonymLookup,
      defaultSuggestions,
      fetchValueSuggestions,
      fetchSemanticSuggestions,
    ],
  );

  // -------------------------------------------------------------------
  // Input change handler
  // -------------------------------------------------------------------
  const handleInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value;
      setLocalValue(raw);

      // Update suggestions immediately
      const cursor = e.target.selectionStart ?? raw.length;
      updateSuggestions(raw, cursor);

      // Debounced onChange callback (300ms)
      if (onChangeTimerRef.current) clearTimeout(onChangeTimerRef.current);
      onChangeTimerRef.current = setTimeout(() => {
        const parsed = parseSearchQuery(raw, synonymLookup ?? undefined);
        onChange(raw, parsed);
      }, 300);
    },
    [onChange, synonymLookup, updateSuggestions],
  );

  // -------------------------------------------------------------------
  // Suggestion insertion
  // -------------------------------------------------------------------

  /** Find the word end from a given position in the query. */
  const getWordEndAt = useCallback((q: string, pos: number): number => {
    let end = pos;
    while (end < q.length && !/\s/.test(q[end])) end++;
    return end;
  }, []);

  const insertSuggestion = useCallback(
    (item: SuggestionItem, mode: SuggestionMode) => {
      if (!inputRef.current || !contextRef) return;

      const q = localValue;
      const replaceStart = contextRef.replaceStart;
      const cursorPos = inputRef.current.selectionStart ?? q.length;
      const replaceEnd = getWordEndAt(q, cursorPos);

      let insertion: string;
      if (mode === 'value') {
        // Quote values that contain spaces
        const escaped = item.value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        const quoted = item.value.includes(' ') ? `"${escaped}"` : item.value;
        // List operators chain values with a comma so the value dropdown stays
        // open for the next value; scalar operators end the filter with a space.
        insertion = quoted + (contextRef.listValue ? ', ' : ' ');
      } else if (mode === 'field') {
        // Quote names with non-identifier chars so the parser's unquoted-
        // identifier rule doesn't truncate the token.
        const display = item.text || item.value;
        const needsQuote = /[^a-zA-Z0-9_]/.test(display);
        insertion = (needsQuote ? `"${display.replace(/"/g, '\\"')}"` : display) + ' ';
      } else {
        insertion = item.value + ' ';
      }

      const before = q.substring(0, replaceStart);
      const after = q.substring(replaceEnd);
      const newValue = before + insertion + after;
      const newCursor = replaceStart + insertion.length;

      setLocalValue(newValue);
      setShowSuggestions(false);
      setSuggestions([]);
      setSelectedIndex(-1);

      // Set value on the DOM input element and reposition cursor
      inputRef.current.value = newValue;
      inputRef.current.focus();
      inputRef.current.setSelectionRange(newCursor, newCursor);

      // After inserting a field -> show operators
      // After inserting an operator -> show values
      // After inserting a value -> fire onChange and show next field
      //
      // A value insertion completes a filter; so does picking a valueless
      // operator (EMPTY / !EMPTY), which has no value step to fire onChange
      // later. Apply the finished filter immediately in both cases — otherwise
      // a valueless filter never reaches the parent until the user manually
      // edits the input.
      if (mode === 'value' || (mode === 'operator' && isValuelessOperator(item.value))) {
        const parsed = parseSearchQuery(newValue, synonymLookup ?? undefined);
        onChange(newValue, parsed);
      }

      // Trigger next suggestions after a tick (cursor needs to settle)
      setTimeout(() => {
        if (inputRef.current) {
          const cursor = inputRef.current.selectionStart ?? newValue.length;
          updateSuggestions(newValue, cursor);
        }
      }, 50);
    },
    [localValue, contextRef, synonymLookup, onChange, updateSuggestions, getWordEndAt],
  );

  const handleSelect = useCallback(
    (item: SuggestionItem) => {
      insertSuggestion(item, suggestionMode);
    },
    [insertSuggestion, suggestionMode],
  );

  // -------------------------------------------------------------------
  // Keyboard navigation
  // -------------------------------------------------------------------
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (!showSuggestions || suggestions.length === 0) return;

      const total = suggestions.length;

      switch (e.key) {
        case 'Tab': {
          e.preventDefault();
          if (e.shiftKey) {
            setSelectedIndex((prev) => (prev <= 0 ? total - 1 : prev - 1));
          } else {
            setSelectedIndex((prev) => (prev >= total - 1 ? 0 : prev + 1));
          }
          break;
        }

        case 'ArrowDown': {
          if (selectedIndex < 0) return;
          e.preventDefault();
          setSelectedIndex((prev) => Math.min(prev + 1, total - 1));
          break;
        }

        case 'ArrowUp': {
          if (selectedIndex < 0) return;
          e.preventDefault();
          setSelectedIndex((prev) => Math.max(prev - 1, 0));
          break;
        }

        case 'ArrowRight': {
          if (selectedIndex < 0) return;
          if (suggestionMode !== 'field') {
            e.preventDefault();
            setSelectedIndex((prev) => Math.min(prev + 1, total - 1));
            break;
          }
          e.preventDefault();
          const groupsR = groupBySource(suggestions);
          let colR = 0, rowR = 0, acc = 0;
          for (let c = 0; c < groupsR.length; c++) {
            if (selectedIndex < acc + groupsR[c].items.length) {
              colR = c;
              rowR = selectedIndex - acc;
              break;
            }
            acc += groupsR[c].items.length;
          }
          if (colR < groupsR.length - 1) {
            const nextCol = colR + 1;
            let base = 0;
            for (let c = 0; c < nextCol; c++) base += groupsR[c].items.length;
            setSelectedIndex(base + Math.min(rowR, groupsR[nextCol].items.length - 1));
          }
          break;
        }

        case 'ArrowLeft': {
          if (selectedIndex < 0) return;
          if (suggestionMode !== 'field') {
            e.preventDefault();
            setSelectedIndex((prev) => Math.max(prev - 1, 0));
            break;
          }
          e.preventDefault();
          const groupsL = groupBySource(suggestions);
          let colL = 0, rowL = 0, accL = 0;
          for (let c = 0; c < groupsL.length; c++) {
            if (selectedIndex < accL + groupsL[c].items.length) {
              colL = c;
              rowL = selectedIndex - accL;
              break;
            }
            accL += groupsL[c].items.length;
          }
          if (colL > 0) {
            const prevCol = colL - 1;
            let base = 0;
            for (let c = 0; c < prevCol; c++) base += groupsL[c].items.length;
            setSelectedIndex(base + Math.min(rowL, groupsL[prevCol].items.length - 1));
          }
          break;
        }

        case 'Enter': {
          if (selectedIndex >= 0 && selectedIndex < total) {
            e.preventDefault();
            handleSelect(suggestions[selectedIndex]);
          } else {
            // No selection — dismiss suggestions, let normal Enter behavior proceed
            setShowSuggestions(false);
          }
          break;
        }

        case 'Escape': {
          e.preventDefault();
          setShowSuggestions(false);
          setSelectedIndex(-1);
          break;
        }
      }
    },
    [showSuggestions, suggestions, selectedIndex, handleSelect],
  );

  // -------------------------------------------------------------------
  // Focus / blur
  // -------------------------------------------------------------------
  const handleFocus = useCallback(() => {
    if (inputRef.current) {
      const cursor = inputRef.current.selectionStart ?? localValue.length;
      updateSuggestions(localValue, cursor);
    }
  }, [localValue, updateSuggestions]);

  const handleBlur = useCallback(() => {
    // Small delay so onMouseDown in SuggestionPanel fires before blur hides the panel
    setTimeout(() => {
      setShowSuggestions(false);
      setSelectedIndex(-1);
    }, 150);
  }, []);

  // Click inside input (cursor may have moved)
  const handleClick = useCallback(() => {
    if (inputRef.current) {
      const cursor = inputRef.current.selectionStart ?? localValue.length;
      updateSuggestions(localValue, cursor);
    }
  }, [localValue, updateSuggestions]);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      if (onChangeTimerRef.current) clearTimeout(onChangeTimerRef.current);
      if (semanticTimerRef.current) clearTimeout(semanticTimerRef.current);
    };
  }, []);

  return (
    <div className={styles.searchWrap}>
      <svg
        className={styles.searchIcon}
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <input
        ref={inputRef}
        type="text"
        className={`${styles.searchInput} ${showSuggestions && suggestions.length > 0 ? styles.searchInputExpanded : ''}`}
        placeholder="Search your asset inventory..."
        value={localValue}
        onChange={handleInput}
        onKeyDown={handleKeyDown}
        onFocus={handleFocus}
        onBlur={handleBlur}
        onClick={handleClick}
        autoComplete="off"
      />
      {showSuggestions && (
        <SuggestionPanel
          mode={suggestionMode}
          items={suggestions}
          selectedIndex={selectedIndex}
          onSelect={handleSelect}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Merge helpers
// ---------------------------------------------------------------------------

function mergeSuggestions(
  primary: SuggestionItem[],
  secondary: SuggestionItem[],
): SuggestionItem[] {
  const byField = new Map<string, SuggestionItem>();

  for (const s of primary) {
    const existing = byField.get(s.value);
    if (!existing || (existing.score ?? 0) < (s.score ?? 0)) {
      byField.set(s.value, s);
    }
  }

  for (const s of secondary) {
    const existing = byField.get(s.value);
    if (!existing || (existing.score ?? 0) < (s.score ?? 0)) {
      byField.set(s.value, s);
    }
  }

  const results = Array.from(byField.values());
  results.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  return results.slice(0, 20);
}
