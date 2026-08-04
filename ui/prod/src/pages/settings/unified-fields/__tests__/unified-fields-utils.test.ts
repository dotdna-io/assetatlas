// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import {
  functionsForType,
  functionsForSources,
  inferDataType,
  validateDefinition,
} from '../unified-fields-utils';

describe('functionsForType', () => {
  it('offers coalesce + union for text/string types', () => {
    expect(functionsForType('string').map((f) => f.value)).toEqual(['coalesce', 'union']);
    expect(functionsForType('string_array').map((f) => f.value)).toEqual(['coalesce', 'union']);
  });
  it('adds max/min for date and number types', () => {
    expect(functionsForType('timestamp').map((f) => f.value)).toEqual(
      ['coalesce', 'union', 'max', 'min'],
    );
    expect(functionsForType('integer').map((f) => f.value)).toEqual(
      ['coalesce', 'union', 'max', 'min'],
    );
  });
  it('labels are human-friendly', () => {
    expect(functionsForType('string').find((f) => f.value === 'coalesce')!.label)
      .toMatch(/first non-empty/i);
  });
});

describe('functionsForSources', () => {
  const txt = (k: string) => ({ encoded_key: k, data_type: 'string' });
  const arr = (k: string) => ({ encoded_key: k, data_type: 'string_array' });
  const ts = (k: string) => ({ encoded_key: k, data_type: 'timestamp' });

  it('offers coalesce + union for scalar text sources', () => {
    expect(functionsForSources([txt('a'), txt('b')]).map((f) => f.value)).toEqual([
      'coalesce',
      'union',
    ]);
  });

  it('offers all four functions for scalar date sources', () => {
    expect(functionsForSources([ts('a'), ts('b')]).map((f) => f.value)).toEqual([
      'coalesce',
      'union',
      'max',
      'min',
    ]);
  });

  // The real-world trap: identity fields (Hostname, FQDN, …) are STRING_ARRAY in
  // the catalog. coalesce/max/min require scalar sources (validateDefinition +
  // the backend reject arrays), so they must NOT be offered — leaving only union.
  // Without this the dropdown defaults to coalesce and Save never enables.
  it('offers only union when any source is an array', () => {
    expect(functionsForSources([arr('a'), arr('b')]).map((f) => f.value)).toEqual(['union']);
    expect(functionsForSources([txt('a'), arr('b')]).map((f) => f.value)).toEqual(['union']);
  });

  it('falls back to coalesce + union for an empty source list', () => {
    expect(functionsForSources([]).map((f) => f.value)).toEqual(['coalesce', 'union']);
  });
});

describe('inferDataType', () => {
  it('returns the common type when all sources agree', () => {
    expect(inferDataType([{ data_type: 'string' }, { data_type: 'string' }])).toBe('string');
  });
  it('treats string and string_array as compatible (text), result string', () => {
    expect(inferDataType([{ data_type: 'string' }, { data_type: 'string_array' }])).toBe('string');
  });
  it('treats scalar jsonb as text (compatible with string) — backend parity', () => {
    // The backend _TEXT set includes JSONB; the frontend must agree or it would
    // wrongly reject a jsonb source as an incompatible type.
    expect(inferDataType([{ data_type: 'jsonb' }, { data_type: 'string' }])).toBe('string');
  });
  it('returns null for incompatible mixes', () => {
    expect(inferDataType([{ data_type: 'string' }, { data_type: 'timestamp' }])).toBeNull();
  });
  it('returns null for an empty list', () => {
    expect(inferDataType([])).toBeNull();
  });
});

const txt = (k: string) => ({ encoded_key: k, data_type: 'string' });
const arr = (k: string) => ({ encoded_key: k, data_type: 'string_array' });

describe('validateDefinition', () => {
  it('requires a name', () => {
    expect(validateDefinition({ name: '', function: 'coalesce', sources: [txt('a'), txt('b')] }, []))
      .toContain('Name is required');
  });
  it('rejects a name already in use (case-insensitive)', () => {
    expect(validateDefinition({ name: 'Lentity', function: 'coalesce', sources: [txt('a'), txt('b')] }, ['lentity']))
      .toContain('A field named "Lentity" already exists');
  });
  it('requires at least two sources', () => {
    expect(validateDefinition({ name: 'x', function: 'coalesce', sources: [txt('a')] }, []))
      .toContain('Select at least two source fields');
  });
  it('rejects a function not valid for the source type', () => {
    expect(validateDefinition({ name: 'x', function: 'max', sources: [txt('a'), txt('b')] }, []))
      .toContain('Most recent / highest is not valid for text fields');
  });
  it('rejects incompatible source types', () => {
    expect(validateDefinition({ name: 'x', function: 'coalesce', sources: [txt('a'), { encoded_key: 'b', data_type: 'timestamp' }] }, []))
      .toContain('Selected fields must share a compatible type');
  });
  it('returns [] for a valid draft', () => {
    expect(validateDefinition({ name: 'lentity', function: 'coalesce', sources: [txt('a'), txt('b')] }, [])).toEqual([]);
  });

  // D6: the backend (validate_compatibility) requires SCALAR sources for
  // coalesce/max/min — array sources (string_array/jsonb_array) are rejected.
  // union may take any. The local typeClass lumps arrays into the text class,
  // so without this rule the modal would let a user submit a draft the server
  // then rejects with a 422.
  describe('scalar-source rule for coalesce/max/min', () => {
    it('rejects a coalesce with a multi-value (array) source', () => {
      expect(
        validateDefinition({ name: 'x', function: 'coalesce', sources: [txt('a'), arr('b')] }, []),
      ).toContain('Coalesce — first non-empty requires single-value source fields');
    });

    it('rejects a coalesce where every source is an array', () => {
      const errors = validateDefinition({ name: 'x', function: 'coalesce', sources: [arr('a'), arr('b')] }, []);
      expect(errors.some((e) => /single-value/i.test(e))).toBe(true);
    });

    it('allows a union with array sources (union is exempt)', () => {
      expect(
        validateDefinition({ name: 'x', function: 'union', sources: [txt('a'), arr('b')] }, []),
      ).toEqual([]);
    });

    it('allows a union where every source is an array', () => {
      expect(
        validateDefinition({ name: 'x', function: 'union', sources: [arr('a'), arr('b')] }, []),
      ).toEqual([]);
    });

    it('allows a coalesce over scalar jsonb + string (both text-class, scalar)', () => {
      const jsonb = (k: string) => ({ encoded_key: k, data_type: 'jsonb' });
      expect(
        validateDefinition({ name: 'x', function: 'coalesce', sources: [jsonb('a'), txt('b')] }, []),
      ).toEqual([]);
    });

    it('does not add the scalar error when the function is already invalid for the type', () => {
      // max over text sources fails the type check first (backend returns that
      // error and stops before the scalar check) — we must not pile on.
      const errors = validateDefinition({ name: 'x', function: 'max', sources: [txt('a'), arr('b')] }, []);
      expect(errors).toContain('Most recent / highest is not valid for text fields');
      expect(errors.some((e) => /single-value/i.test(e))).toBe(false);
    });
  });
});
