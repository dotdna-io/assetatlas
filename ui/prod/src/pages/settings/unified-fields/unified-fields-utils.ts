// SPDX-License-Identifier: AGPL-3.0-or-later
export type MergeFunction = 'coalesce' | 'union' | 'max' | 'min';
export interface FunctionOption { value: MergeFunction; label: string }

const LABELS: Record<MergeFunction, string> = {
  coalesce: 'Coalesce — first non-empty',
  union: 'Union — collect all values',
  max: 'Most recent / highest',
  min: 'Earliest / lowest',
};
const DATE_NUM = new Set(['timestamp', 'date', 'datetime', 'integer', 'float', 'number']);

export function functionsForType(dataType: string): FunctionOption[] {
  const codes: MergeFunction[] = ['coalesce', 'union'];
  if (DATE_NUM.has(dataType)) codes.push('max', 'min');
  return codes.map((value) => ({ value, label: LABELS[value] }));
}

function typeClass(t: string): 'text' | 'date' | 'number' | 'other' {
  // 'jsonb' is scalar text on the backend (_TEXT in definition.py); keep parity
  // so a scalar jsonb source isn't wrongly rejected as an incompatible type.
  if (t === 'string' || t === 'string_array' || t === 'jsonb_array' || t === 'jsonb') return 'text';
  if (t === 'timestamp' || t === 'date' || t === 'datetime') return 'date';
  if (t === 'integer' || t === 'float' || t === 'number') return 'number';
  return 'other';
}

// Multi-value (array) data types. Mirrors the backend `_ARRAY` set
// (src/modules/fields/unified/definition.py): coalesce/max/min reduce to a
// single value and require scalar inputs, so the server rejects array sources
// for those functions. union is exempt (it collects all values).
const ARRAY_TYPES = new Set(['string_array', 'jsonb_array']);

function isArrayType(t: string): boolean {
  return ARRAY_TYPES.has(t);
}

// True when any source is a multi-value (array) type. coalesce/max/min collapse
// to a single value and the backend rejects array sources for them, so this
// single predicate gates both the offerable functions (functionsForSources) and
// the inline scalar-source error (validateDefinition) — keeping them in lockstep.
function hasArraySource(sources: { data_type: string }[]): boolean {
  return sources.some((s) => isArrayType(s.data_type));
}

// Functions offerable for a concrete set of resolved sources. Unlike
// functionsForType (which only sees the collapsed type *class*), this honours
// the scalar-vs-array distinction that validateDefinition enforces: coalesce/
// max/min reduce to a single value and the backend rejects array sources for
// them, so when any source is multi-value only `union` is valid. Used to drive
// the modal's function dropdown so it never defaults to a function the form
// would then reject — the cause of "Save never enables" on identity fields
// (Hostname/FQDN/… are STRING_ARRAY).
export function functionsForSources(sources: { data_type: string }[]): FunctionOption[] {
  const base = functionsForType(inferDataType(sources) ?? 'string');
  if (hasArraySource(sources)) {
    return base.filter((o) => o.value === 'union');
  }
  return base;
}

export function inferDataType(sources: { data_type: string }[]): string | null {
  if (sources.length === 0) return null;
  const classes = new Set(sources.map((s) => typeClass(s.data_type)));
  if (classes.size !== 1) return null;
  const [cls] = [...classes];
  if (cls === 'text') return 'string';
  return sources[0].data_type;
}

export interface DraftDefinition {
  name: string;
  function: MergeFunction;
  sources: { encoded_key: string; data_type: string }[];
}

export function validateDefinition(draft: DraftDefinition, existingNames: string[]): string[] {
  const errors: string[] = [];

  if (!draft.name.trim()) {
    errors.push('Name is required');
  } else if (existingNames.some((n) => n.toLowerCase() === draft.name.toLowerCase())) {
    errors.push(`A field named "${draft.name}" already exists`);
  }

  if (draft.sources.length < 2) {
    errors.push('Select at least two source fields');
  }

  const inferred = inferDataType(draft.sources);

  if (inferred === null) {
    if (draft.sources.length >= 2) {
      errors.push('Selected fields must share a compatible type');
    }
  } else {
    const validFunctions = functionsForType(inferred).map((f) => f.value);
    if (!validFunctions.includes(draft.function)) {
      const label = LABELS[draft.function];
      const cls = typeClass(inferred);
      errors.push(`${label} is not valid for ${cls} fields`);
    } else if (
      (draft.function === 'coalesce' || draft.function === 'max' || draft.function === 'min') &&
      hasArraySource(draft.sources)
    ) {
      // coalesce/max/min collapse to one value — array sources are rejected by
      // the backend. Only flagged once the function is otherwise valid for the
      // type, matching the server's check order (type first, then scalar).
      errors.push(`${LABELS[draft.function]} requires single-value source fields`);
    }
  }

  return errors;
}
