// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from 'vitest';

import type { FieldCatalogEntry } from '@/types/field-id';
import { getDisplayName, confidenceBandToFilter, findFieldEntry, generateCSV, groupByCategory } from '../explorer-utils';

// Minimal FieldCatalogEntry builder — these tests exercise getDisplayName /
// findFieldEntry, which key off encoded_key + display_name only, so callers
// specify just those and let the rest default.
function fieldEntry(
  over: Partial<FieldCatalogEntry> &
    Pick<FieldCatalogEntry, 'encoded_key' | 'display_name'>,
): FieldCatalogEntry {
  return {
    field_id: { kind: 'static', name: over.display_name },
    category: 'virtual',
    data_type: 'string',
    operator_specs: [],
    is_array: false,
    is_grouped: false,
    member_sources: null,
    ...over,
  };
}

const CONFIDENCE_LEVEL_ENTRY = fieldEntry({
  encoded_key: 'virtual:confidence_level',
  display_name: 'Confidence Level',
  field_id: { kind: 'virtual', name: 'confidence_level' },
});

const CONFIDENCE_SCORE_ENTRY = fieldEntry({
  encoded_key: 'static:Confidence%20Score',
  display_name: 'Confidence Score',
  field_id: { kind: 'static', name: 'Confidence Score' },
  category: 'advanced',
  data_type: 'float',
});

// asset_label carried the 'Asset' override that broke its search round-trip the
// same way the confidence fields did. (The frontend FieldId type models only
// virtual:confidence_level, so field_id defaults here — immaterial to these
// tests, which resolve by encoded_key + display_name.)
const ASSET_LABEL_ENTRY = fieldEntry({
  encoded_key: 'virtual:asset_label',
  display_name: 'Asset Label',
});

describe('getDisplayName', () => {
  it('returns the canonical name for asset_label — override removed (#704)', () => {
    expect(getDisplayName({ display_name: 'Asset Label' }))
      .toBe('Asset Label');
  });

  it('surfaces the resolvable "Confidence Level" name — no override (#704)', () => {
    // The old override relabelled this to 'Confidence', which collided with the
    // numeric Confidence Score field and resolved to neither — see the
    // round-trip regression block below.
    expect(getDisplayName({ display_name: 'Confidence Level' }))
      .toBe('Confidence Level');
  });

  it('surfaces the real "Confidence Score" name — no override (#704)', () => {
    expect(getDisplayName({ display_name: 'Confidence Score' }))
      .toBe('Confidence Score');
  });

  it('falls back to display_name when no override exists', () => {
    expect(getDisplayName({ display_name: 'Hostname' }))
      .toBe('Hostname');
  });

  it('accepts column-spec shape (name)', () => {
    expect(getDisplayName({ name: 'Asset Label' }))
      .toBe('Asset Label');
  });

  it('returns empty string when nothing is provided', () => {
    expect(getDisplayName({})).toBe('');
  });
});

describe('field display labels round-trip through findFieldEntry (#704)', () => {
  // The search bar inserts getDisplayName(field) into the query string, and
  // ExplorerPage.buildFilters later resolves that token back with
  // findFieldEntry. If the displayed label does not resolve to the SAME entry,
  // the filter is silently dropped and the search does nothing. Display-label
  // overrides broke this: 'Confidence' (shared by both confidence fields) and
  // 'Asset' (asset_label) matched no catalog display_name, so picking those
  // fields was a no-op. Removing the override layer makes the round-trip hold
  // for every field by construction; these cover the fields that were broken.
  const catalog = [CONFIDENCE_LEVEL_ENTRY, CONFIDENCE_SCORE_ENTRY, ASSET_LABEL_ENTRY];

  it.each(catalog)(
    '$display_name: label is the canonical display_name and round-trips to its own entry',
    (entry) => {
      const label = getDisplayName(entry);
      // Assert the label IS the human-readable display_name, not the encoded
      // key. findFieldEntry also matches on encoded_key, so without this a
      // regression where getDisplayName returned the key would still resolve
      // and slip past the round-trip check — exactly the #704 class of bug.
      expect(label).toBe(entry.display_name);
      expect(findFieldEntry(label, catalog)).toBe(entry);
    },
  );
});

describe('groupByCategory', () => {
  // A saved Unified Field is exposed in the catalog with category 'unified'
  // (backend catalog._entry_category). Without an explicit case it fell through
  // the switch into no bucket and never appeared in the column picker, so the
  // field could not be selected as an Explorer column. It belongs with the
  // other first-class scalar fields, i.e. the 'standard' bucket.
  it("routes a 'unified' field into the standard bucket", () => {
    const unified = fieldEntry({
      encoded_key: 'virtual:lentity',
      display_name: 'Merged Lentity',
      category: 'unified',
    });
    const buckets = groupByCategory([unified]);
    expect(buckets.standard).toContain(unified);
  });

  it('keeps the existing buckets intact alongside a unified field', () => {
    const adv = fieldEntry({ encoded_key: 'static:Confidence%20Score', display_name: 'Confidence Score', category: 'advanced' });
    const tag = fieldEntry({ encoded_key: 'tag:Owner', display_name: 'Owner', category: 'tags' });
    const unified = fieldEntry({ encoded_key: 'virtual:lentity', display_name: 'Merged Lentity', category: 'unified' });
    const buckets = groupByCategory([adv, tag, unified]);
    expect(buckets.advanced).toContain(adv);
    expect(buckets.tags).toContain(tag);
    expect(buckets.standard).toContain(unified);
  });
});

describe('confidenceBandToFilter', () => {
  it('maps confirmed → confidence_level eq high (not a Confidence Score numeric filter)', () => {
    const result = confidenceBandToFilter('confirmed');
    expect(result).not.toBeNull();
    // Must use the virtual field display name — NOT the raw numeric 'Confidence Score'
    expect(result!.filter.field).toBe('Confidence Level');
    expect(result!.filter.field).not.toBe('Confidence Score');
    expect(result!.filter.op).toBe('eq');
    expect(result!.filter.value).toBe('high');
    // No hardcoded numeric thresholds in the value
    expect(result!.filter.value).not.toMatch(/0\.\d+/);
    // The label is dropped verbatim into the explorer search box, so the
    // multi-word field name must be quoted — otherwise the parser reads
    // "Confidence" as a stray keyword and "Level" as the field.
    expect(result!.label).toBe('"Confidence Level" = high');
  });

  it('maps under_covered → confidence_level eq medium', () => {
    const result = confidenceBandToFilter('under_covered');
    expect(result).not.toBeNull();
    expect(result!.filter.field).toBe('Confidence Level');
    expect(result!.filter.op).toBe('eq');
    expect(result!.filter.value).toBe('medium');
    expect(result!.label).toBe('"Confidence Level" = medium');
  });

  it('maps shadows → confidence_level eq low', () => {
    const result = confidenceBandToFilter('shadows');
    expect(result).not.toBeNull();
    expect(result!.filter.field).toBe('Confidence Level');
    expect(result!.filter.op).toBe('eq');
    expect(result!.filter.value).toBe('low');
    expect(result!.label).toBe('"Confidence Level" = low');
  });

  it('returns null for unknown band values', () => {
    expect(confidenceBandToFilter('')).toBeNull();
    expect(confidenceBandToFilter('unknown')).toBeNull();
  });

  it('old workaround shape (Confidence Score numeric filter) is NOT produced', () => {
    for (const band of ['confirmed', 'under_covered', 'shadows']) {
      const result = confidenceBandToFilter(band);
      expect(result!.filter.field).not.toBe('Confidence Score');
      expect(result!.filter.op).not.toBe('gte');
      expect(result!.filter.op).not.toBe('lt');
    }
  });
});

describe('generateCSV CSV/formula injection neutralization (#741)', () => {
  // Byte-for-byte convention shared with the backend _cell()/_neutralize_formula
  // (src/api/utils/csv_export.py): a cell whose value begins with = + @ (or a
  // leading TAB / CR), or with - NOT followed by an ASCII digit or '.', is
  // prefixed with a single quote so a spreadsheet app can't run it as a formula.
  // The neutralized CELL bytes below are identical to test_csv_export.py's cases
  // (the backend additionally emits a trailing CRLF — a pre-existing serializer
  // difference, not part of this fix).
  const COLS = [{ key: 'v', name: 'V' }];
  const csv = (val: unknown) => generateCSV([{ v: val }], COLS);

  it('neutralizes a leading =', () => {
    expect(csv('=1+1')).toBe("V\r\n'=1+1");
  });

  it('neutralizes a leading @', () => {
    expect(csv('@SUM(A1)')).toBe("V\r\n'@SUM(A1)");
  });

  it('neutralizes a leading + even for numbers', () => {
    expect(csv('+1')).toBe("V\r\n'+1");
  });

  it('neutralizes a leading tab', () => {
    expect(csv('\t=1')).toBe("V\r\n'\t=1");
  });

  it('neutralizes a leading CR and quotes the embedded CR', () => {
    expect(csv('\r=1')).toBe('V\r\n"\'\r=1"');
  });

  it('leaves negative numbers untouched', () => {
    expect(csv('-5')).toBe('V\r\n-5');
    expect(csv('-5.3')).toBe('V\r\n-5.3');
    expect(csv('-.5')).toBe('V\r\n-.5');
  });

  it('neutralizes a leading dash when not a number', () => {
    expect(csv("-cmd|'/c calc'")).toBe("V\r\n'-cmd|'/c calc'");
    expect(csv('-=1+1')).toBe("V\r\n'-=1+1");
  });

  it('neutralizes before quoting when the value has a comma', () => {
    expect(csv('=a,b')).toBe('V\r\n"\'=a,b"');
  });

  it('joins arrays then neutralizes the joined string as a whole', () => {
    expect(csv(['=a', 'b'])).toBe("V\r\n'=a; b");
  });

  it('leaves a dangerous char that is not in lead position untouched', () => {
    expect(csv('a=b')).toBe('V\r\na=b');
  });

  it('leaves a safe value unchanged', () => {
    expect(csv('host01')).toBe('V\r\nhost01');
  });

  it('neutralizes header labels too (custom field named "=Total")', () => {
    expect(generateCSV([], [{ key: 'v', name: '=Total' }])).toBe("'=Total");
  });
});
