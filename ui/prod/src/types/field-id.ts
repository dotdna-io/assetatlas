// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Shared FieldId discriminated union + canonical string encoding.
 *
 * Mirrors `src/modules/fields/models.py` (Pydantic FieldId) and
 * `src/modules/fields/encoding.py` (encode/decode). Parity is enforced
 * by `src/types/__tests__/field-id.test.ts` against the shared fixture
 * at `tests/fixtures/field-id-encoding.json`.
 *
 * Wire contract:
 *  - JSON bodies: FieldId object.
 *  - Query params + response row keys: percent-encoded canonical string
 *    form (e.g. `static:Hostname`, `ext:Intune%20Full:deviceType`,
 *    `posture_state:secured`, `virtual:confidence_level`).
 */

export type FieldId =
  | { kind: 'static'; name: string }
  | { kind: 'core_enrichment'; name: string }
  | { kind: 'extended_attribute'; source: string; attribute: string }
  | { kind: 'integration_group'; group: string; attribute: string }
  | { kind: 'tag'; name: string }
  | { kind: 'posture'; category: string; read: 'state' | 'output' }
  | { kind: 'virtual'; name: string };

export interface OperatorSpec {
  code: string;
  label: string;
  symbol: string | null;
  aliases: string[];
  arity: 'none' | 'one' | 'list';
  value_type: 'text' | 'number' | 'date' | 'bool';
}

export interface FieldCatalogEntry {
  display_name: string;
  field_id: FieldId;
  encoded_key: string;
  category:
    | 'target'
    | 'advanced'
    | 'extended_attributes'
    | 'tags'
    | 'enrichment'
    | 'posture'
    | 'virtual'
    | 'unified';
  data_type: string;
  operator_specs: OperatorSpec[];
  is_array: boolean;
  is_grouped: boolean;
  /** Filterable, but never projectable/aggregatable/sortable — the backend
   *  omits these unless asked. Consumers that opt in MUST strip them from
   *  column and pivot pickers. */
  filter_only: boolean;
  member_sources: string[] | null;
}

// RFC-3986-strict segment encoder. Parity invariant: output MUST equal
// Python's `urllib.parse.quote(s, safe='')` (see src/modules/fields/encoding.py).
// JavaScript's built-in `encodeURIComponent` leaves `!`, `'`, `(`, `)`, `*`
// unencoded; Python's `quote(safe='')` encodes them. Customer source names
// commonly contain `(` and `)` (e.g. "Intune (Beta)"), so the silent gap
// would produce row-key mismatches between server-emitted keys and
// client-encoded lookups. The replace() step patches the difference.
function encodeSegment(s: string): string {
  return encodeURIComponent(s).replace(
    /[!'()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

export function encodeFieldId(f: FieldId): string {
  switch (f.kind) {
    case 'static':
      return `static:${encodeSegment(f.name)}`;
    case 'core_enrichment':
      return `core:${encodeSegment(f.name)}`;
    case 'extended_attribute':
      return `ext:${encodeSegment(f.source)}:${encodeSegment(f.attribute)}`;
    case 'integration_group':
      return `group:${encodeSegment(f.group)}:${encodeSegment(f.attribute)}`;
    case 'tag':
      return `tag:${encodeSegment(f.name)}`;
    case 'posture':
      return `${f.read === 'state' ? 'posture_state' : 'posture_output'}:${encodeSegment(f.category)}`;
    case 'virtual':
      return `virtual:${encodeSegment(f.name)}`;
  }
}

export function decodeFieldId(s: string): FieldId {
  if (!s) throw new Error('empty FieldId');
  const parts = s.split(':');
  const prefix = parts[0];
  let segs: string[];
  try {
    segs = parts.slice(1).map(decodeURIComponent);
  } catch {
    throw new Error(`FieldId '${s}' contains malformed percent-encoding`);
  }

  function expect(n: number) {
    if (segs.length !== n) {
      throw new Error(
        `FieldId '${s}' has ${segs.length} segment(s); kind '${prefix}' expects ${n}`,
      );
    }
  }

  switch (prefix) {
    case 'static':
      expect(1);
      return { kind: 'static', name: segs[0] };
    case 'core':
      expect(1);
      return { kind: 'core_enrichment', name: segs[0] };
    case 'ext':
      expect(2);
      return { kind: 'extended_attribute', source: segs[0], attribute: segs[1] };
    case 'group':
      expect(2);
      return { kind: 'integration_group', group: segs[0], attribute: segs[1] };
    case 'tag':
      expect(1);
      return { kind: 'tag', name: segs[0] };
    case 'posture_state':
      expect(1);
      return { kind: 'posture', category: segs[0], read: 'state' };
    case 'posture_output':
      expect(1);
      return { kind: 'posture', category: segs[0], read: 'output' };
    case 'virtual':
      expect(1);
      // Any virtual name is valid: built-in computed fields (confidence_level,
      // asset_label, …) AND user-defined Unified Fields, which use
      // `virtual:<slug>`. The backend (src/modules/fields/models.py VirtualField)
      // accepts any name; the frontend must match, or a unified field's encoded
      // key throws here (#784). Validity is the catalog's job, not decode's.
      return { kind: 'virtual', name: segs[0] };
    default:
      throw new Error(`unknown FieldId prefix: '${prefix}'`);
  }
}

/**
 * Accept either an encoded-key string or an already-typed FieldId and
 * return a FieldId (or null if the input can't be decoded). Logs a
 * dev-time warning for string inputs that fail to decode — silent nulls
 * would hide template bugs like raw DB column names leaking to the wire.
 */
export function coerceFieldId(input: unknown, slot?: string): FieldId | null {
  if (!input) return null;
  if (typeof input === 'string') {
    try {
      return decodeFieldId(input);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn(
        `[field-id] coerceFieldId: could not decode '${input}'` +
          (slot ? ` for slot '${slot}'` : '') +
          ` — dropping. ${(err as Error).message}`,
      );
      return null;
    }
  }
  if (typeof input === 'object' && input !== null && 'kind' in input) {
    return input as FieldId;
  }
  return null;
}
