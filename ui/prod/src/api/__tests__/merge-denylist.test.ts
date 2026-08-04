// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

import fixture from '../../../../../tests/fixtures/merge-denylist-match-types.json';
import {
  useDenylistMatchPreview,
  MATCH_TYPES_BY_TYPE,
  PREVIEW_FIELD_ID,
  PREVIEW_LIMIT,
  type DenylistDraft,
  type IdentifierType,
} from '../merge-denylist';
import { apiClient } from '../client';
import { setupMutationTest } from './helpers';

// The tab's own tests mock this hook out, so this is where the counting itself
// is checked: what it asks GET /assets/values for, and what it makes of the
// answer. Both halves matter — the request is the only thing narrowing 25k MACs
// down to the 500 the endpoint will return, and the predicate is the only thing
// that makes those 500 an answer rather than a sample.
vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    del: vi.fn(),
  },
}));

const mockGet = apiClient.get as ReturnType<typeof vi.fn>;

const MAC_FIELD = 'static:MAC%20Address';
const HOSTNAME_FIELD = 'static:Hostname';

function draft(over: Partial<DenylistDraft>): DenylistDraft {
  return {
    identifier_type: 'mac',
    match_type: 'exact',
    raw_value: '',
    range_start: '',
    range_end: '',
    ...over,
  };
}

/** Answer the M call with `total`, and the candidate call with `values`. */
function respondWith(values: string[], total = 25913) {
  mockGet.mockImplementation((_url: string, opts?: { params?: Record<string, unknown> }) =>
    Promise.resolve(
      opts?.params?.limit === 1
        ? { field: 'f', values: [], total_distinct: total }
        : { field: 'f', values: values.map((value) => ({ value, count: 1 })), total_distinct: 0 },
    ),
  );
}

function candidateParams(): Record<string, unknown> {
  const call = mockGet.mock.calls.find((c) => c[1]?.params?.limit !== 1);
  return call?.[1]?.params ?? {};
}

/** `settles: false` for drafts the hook is expected to ignore entirely. */
async function preview(d: DenylistDraft | null, settles = true) {
  const { wrapper } = setupMutationTest();
  const { result } = renderHook(() => useDenylistMatchPreview(d), { wrapper });
  if (settles) await waitFor(() => expect(result.current.data).toBeDefined());
  return result;
}

// The Python half is tests/unit/merge_denylist/test_encoding.py. Same
// shared-fixture shape as field-id.test.ts: the encoder is the source of truth
// and these constants are a copy of it, so the copy is pinned rather than
// trusted.
describe('parity with the encoder', () => {
  it('offers exactly the match-type pairs encoding.py allows', () => {
    expect(MATCH_TYPES_BY_TYPE).toEqual(fixture.match_types_by_type);
  });

  it('previews against the catalog fields the fixture pins', () => {
    const names = Object.fromEntries(
      Object.entries(PREVIEW_FIELD_ID).map(([k, v]) => [k, (v as { name: string }).name]),
    );
    const expected = Object.fromEntries(
      Object.entries(fixture.preview_fields).map(([k, v]) => [k, v.display_name]),
    );
    expect(names).toEqual(expected);
  });

  it('has a match-type list for every identifier type it can preview', () => {
    expect(Object.keys(MATCH_TYPES_BY_TYPE).sort()).toEqual(
      Object.keys(PREVIEW_FIELD_ID).sort(),
    );
  });
});

describe('useDenylistMatchPreview', () => {
  beforeEach(() => vi.clearAllMocks());

  describe('what it asks for', () => {
    it('narrows a MAC prefix by the colon-grouped hex assets.mac is stored in', async () => {
      // 'bc2411%' would match nothing: the column holds 'bc:24:11:22:33:44'.
      respondWith([]);
      await preview(draft({ match_type: 'prefix', raw_value: 'BC-24-11-*' }));

      expect(candidateParams()).toMatchObject({
        field: MAC_FIELD,
        search: 'bc:24:11',
        limit: PREVIEW_LIMIT,
      });
    });

    it('keeps an odd nibble on the grouped prefix', async () => {
      respondWith([]);
      await preview(draft({ match_type: 'prefix', raw_value: 'bc24111*' }));
      expect(candidateParams().search).toBe('bc:24:11:1');
    });

    it('narrows a suffix by asset, since search can only anchor a prefix', async () => {
      respondWith([]);
      await preview(
        draft({ identifier_type: 'hostname', match_type: 'suffix', raw_value: '*.Corp.Local' }),
      );

      const params = candidateParams();
      expect(params.search).toBeUndefined();
      expect(JSON.parse(params.filters as string)).toEqual([
        { field_id: { kind: 'static', name: 'Hostname' }, op: 'ends', value: '.corp.local' },
      ]);
      expect(params.field).toBe(HOSTNAME_FIELD);
    });

    it('narrows a range by the tightest prefix enclosing both bounds', async () => {
      respondWith([]);
      await preview(
        draft({
          match_type: 'range',
          range_start: 'bc:24:11:00:00:00',
          range_end: 'bc:24:11:ff:ff:ff',
        }),
      );
      expect(candidateParams().search).toBe('bc:24:11');
    });

    it('sends no search for a range whose bounds share nothing', async () => {
      // The widest range there is: nothing to narrow by, so the fetch is the
      // fleet's most common MACs and the count comes back capped.
      respondWith([]);
      await preview(
        draft({
          match_type: 'range',
          range_start: '00:00:00:00:00:00',
          range_end: 'ff:ff:ff:ff:ff:ff',
        }),
      );
      expect(candidateParams().search).toBeUndefined();
    });

    it('asks for the denominator separately and unnarrowed', async () => {
      // total_distinct ignores `search`, so M has to come from its own call.
      respondWith([]);
      await preview(draft({ match_type: 'prefix', raw_value: 'bc:24:11:*' }));

      const totals = mockGet.mock.calls.filter((c) => c[1]?.params?.limit === 1);
      expect(totals).toHaveLength(1);
      expect(totals[0][1].params).toEqual({ field: MAC_FIELD, limit: 1 });
    });
  });

  describe('what it counts', () => {
    it('counts only the values that really match, not everything fetched', async () => {
      // An unescaped '_' in `search` is a LIKE wildcard and a suffix fetch
      // returns whole assets' worth of values, so the response is a superset by
      // design — the predicate is what makes the number true.
      respondWith(['bc:24:11:22:33:44', 'bc:24:11:00:00:01', 'aa:bb:cc:dd:ee:ff']);
      const r = await preview(draft({ match_type: 'prefix', raw_value: 'bc:24:11:*' }));

      expect(r.current.data).toEqual({ matched: 2, total: 25913, capped: false });
    });

    it('matches hostnames case-insensitively, as the stored values are mixed case', async () => {
      // assets.hostname keeps the display spelling ('THBKKLTMKT03'); the
      // denylist pattern is lowercased. A case-sensitive count reads as zero.
      respondWith(['WEB01.CORP.LOCAL', 'web02.corp.local', 'db01.other.local']);
      const r = await preview(
        draft({ identifier_type: 'hostname', match_type: 'suffix', raw_value: '*.corp.local' }),
      );

      expect(r.current.data?.matched).toBe(2);
    });

    it('counts a range by its bounds, not by text', async () => {
      // 'bc:24:11:80:00:00' sorts inside the enclosing prefix but outside the
      // range: only arithmetic separates them.
      respondWith([
        'bc:24:11:00:00:05',
        'bc:24:11:00:00:10',
        'bc:24:11:00:00:11',
        'bc:24:11:80:00:00',
      ]);
      const r = await preview(
        draft({
          match_type: 'range',
          range_start: 'bc:24:11:00:00:05',
          range_end: 'bc:24:11:00:00:10',
        }),
      );

      expect(r.current.data?.matched).toBe(2);
    });

    it('flags a full page as capped, so the caller reports a floor', async () => {
      respondWith(Array.from({ length: PREVIEW_LIMIT }, (_, i) => `bc:24:11:00:00:${i % 100}`));
      const r = await preview(draft({ match_type: 'prefix', raw_value: 'bc:24:11:*' }));

      expect(r.current.data?.capped).toBe(true);
    });
  });

  describe('what it refuses to ask', () => {
    it.each([
      ['no draft at all', null],
      ['a half-typed MAC', draft({ raw_value: 'bc:24:11' })],
      ['a prefix with no literal', draft({ match_type: 'prefix', raw_value: '*' })],
      ['an interior glob', draft({ identifier_type: 'hostname', match_type: 'prefix', raw_value: 'web*01*' })],
      [
        'a reversed range',
        draft({ match_type: 'range', range_start: 'bc:24:11:ff:ff:ff', range_end: 'bc:24:11:00:00:00' }),
      ],
      ['a range missing a bound', draft({ match_type: 'range', range_start: 'bc:24:11:00:00:00' })],
      // Everything the encoder 400s on has to be silent too. Fetching for these
      // returns nothing, which would render as "Matches none of your 25,913 MAC
      // addresses" — a broken entry reading exactly like a narrow one.
      ['a non-hex MAC prefix', draft({ match_type: 'prefix', raw_value: 'zz:zz:zz:*' })],
      [
        'a MAC prefix longer than a MAC',
        draft({ match_type: 'prefix', raw_value: 'bc:24:11:22:33:44:55:*' }),
      ],
      [
        'an exact hostname holding a wildcard',
        draft({ identifier_type: 'hostname', match_type: 'exact', raw_value: 'web*' }),
      ],
      [
        'a range bound that is not hex',
        draft({
          match_type: 'range',
          // parseInt would stop at the 'z' and take the bound as 0xbc2411.
          range_start: 'bc:24:11:zz:zz:zz',
          range_end: 'bc:24:11:ff:ff:ff',
        }),
      ],
    ])('stays quiet for %s', async (_name, d) => {
      respondWith([]);
      const r = await preview(d as DenylistDraft | null, false);

      // Nothing to say and nothing to ask: the encoder is what tells the admin
      // an entry is malformed, on save, in words they can act on.
      expect(mockGet).not.toHaveBeenCalled();
      expect(r.current.data).toBeUndefined();
      expect(r.current.isPending).toBe(false);
    });

    // Guards the refusals above from over-reaching. The encoder is asymmetric
    // here and the preview has to be asymmetric the same way, or it goes quiet
    // on entries that are perfectly storable.
    it.each([
      // canonical_mac_component only cleans to alnum, so this really is stored.
      ['an exact MAC that is not hex', draft({ raw_value: 'zz:zz:zz:zz:zz:zz' })],
      // A serial has no prefix/suffix form, so '*' is a literal character.
      ['an exact serial holding a wildcard', draft({ identifier_type: 'serial', raw_value: 'AB*123' })],
      // Refused on save by the OUI floor — but the count is *why*, so show it.
      ['a MAC prefix under the OUI floor', draft({ match_type: 'prefix', raw_value: 'b*' })],
    ])('still previews %s', async (_name, d) => {
      respondWith([]);
      const r = await preview(d);

      expect(mockGet).toHaveBeenCalled();
      expect(r.current.data).toBeDefined();
    });
  });
});
