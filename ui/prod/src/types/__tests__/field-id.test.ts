// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import fixtures from '../../../../../tests/fixtures/field-id-encoding.json';
import { encodeFieldId, decodeFieldId, type FieldId } from '../field-id';

interface FixtureCase {
  encoded: string;
  field_id: FieldId;
}

const cases = fixtures as FixtureCase[];

describe('FieldId encoding parity with Python fixtures', () => {
  cases.forEach((c) => {
    it(`encode(${JSON.stringify(c.field_id)}) = ${c.encoded}`, () => {
      expect(encodeFieldId(c.field_id)).toBe(c.encoded);
    });
    it(`decode(${c.encoded}) = ${JSON.stringify(c.field_id)}`, () => {
      expect(decodeFieldId(c.encoded)).toEqual(c.field_id);
    });
  });
});

describe('FieldId round-trip', () => {
  it('encode(decode(s)) === s for every fixture case', () => {
    for (const c of cases) {
      expect(encodeFieldId(decodeFieldId(c.encoded))).toBe(c.encoded);
    }
  });
  it('decode(encode(f)) deep-equals f for every fixture case', () => {
    for (const c of cases) {
      expect(decodeFieldId(encodeFieldId(c.field_id))).toEqual(c.field_id);
    }
  });
});

describe('decodeFieldId handles percent-encoded colons in segments', () => {
  it('keeps `Source With:colon` intact on round-trip', () => {
    const f: FieldId = {
      kind: 'extended_attribute',
      source: 'Source With:colon',
      attribute: 'attr',
    };
    const encoded = encodeFieldId(f);
    expect(encoded).toBe('ext:Source%20With%3Acolon:attr');
    // Literal `:` between source and attribute is the only real separator;
    // the inner `:` is encoded as `%3A` so split(':') yields exactly 2 segments.
    expect(encoded.split(':')).toHaveLength(3);
    expect(decodeFieldId(encoded)).toEqual(f);
  });
});

describe('decodeFieldId rejections', () => {
  it('rejects unknown prefix', () => {
    expect(() => decodeFieldId('bogus:Hostname')).toThrow();
  });
  it('rejects empty string', () => {
    expect(() => decodeFieldId('')).toThrow();
  });
  it('rejects wrong segment count for static', () => {
    expect(() => decodeFieldId('static:Hostname:extra')).toThrow();
  });
  it('rejects wrong segment count for ext', () => {
    expect(() => decodeFieldId('ext:OnlyOneSegment')).toThrow();
  });
});

describe('decodeFieldId virtual fields', () => {
  it('decodes built-in and unified-field virtual names alike (#784)', () => {
    // The virtual kind covers built-in computed fields (confidence_level, …)
    // AND user-defined Unified Fields (`virtual:<slug>`). decode must accept any
    // name — validity is the catalog/backend's job. Previously this threw for
    // anything but confidence_level, so a unified field's key was undecodable.
    expect(decodeFieldId('virtual:confidence_level')).toEqual({
      kind: 'virtual',
      name: 'confidence_level',
    });
    expect(decodeFieldId('virtual:lentity')).toEqual({
      kind: 'virtual',
      name: 'lentity',
    });
  });
});
