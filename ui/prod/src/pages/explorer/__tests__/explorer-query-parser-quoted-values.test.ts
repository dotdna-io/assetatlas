// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * A quoted value may not start a filter.
 *
 * Passes 1 (empty/notempty) and 2 (any/all/none/between) scan the raw string
 * with regexes that were not quote-aware, so an operator WORD inside a quoted
 * value started a filter of its own and ate the real one. Values are free text —
 * model names, locations, alert titles — so this was never a corner case:
 * `Model = "OptiPlex 7070 All in One"` parsed as field `all` op `any`, leaving
 * `model = "OptiPlex`.
 *
 * The invariant, stated once: no pass may START a filter inside a quoted span.
 * A quoted FIELD name starts AT the quote, not inside it, so it still parses.
 */
import { describe, it, expect } from 'vitest';

import { parseSearchQuery } from '../explorer-query-parser';

describe('quoted values containing operator words', () => {
  it('does not let "in" inside a value start an any-of filter', () => {
    // One of the two live alert rule names this breaks.
    expect(parseSearchQuery('Alert = "Out-of-profile device in segment"')).toEqual({
      filters: [{ field: 'alert', op: 'eq', value: 'Out-of-profile device in segment' }],
      keyword: '',
    });
  });

  it('does not let "in" inside a longer value split the filter', () => {
    expect(parseSearchQuery('Alert = "Unprotected newcomer in secured segment"')).toEqual({
      filters: [{ field: 'alert', op: 'eq', value: 'Unprotected newcomer in secured segment' }],
      keyword: '',
    });
  });

  it('does not let "all"/"in" inside a model name split the filter', () => {
    // Pre-existing breakage, pinned so the fix is a general parser property
    // rather than something that happens to suit alert titles.
    expect(parseSearchQuery('Model = "OptiPlex 7070 All in One"')).toEqual({
      filters: [{ field: 'model', op: 'eq', value: 'OptiPlex 7070 All in One' }],
      keyword: '',
    });
  });

  it('does not let "between" inside a location split the filter', () => {
    expect(parseSearchQuery('Location = "Building A between towers"')).toEqual({
      filters: [{ field: 'location', op: 'eq', value: 'Building A between towers' }],
      keyword: '',
    });
  });

  it('does not let "empty" inside a value start a valueless filter', () => {
    // Pass 1 has the same defect as pass 2.
    expect(parseSearchQuery('Model = "Rack empty slot"')).toEqual({
      filters: [{ field: 'model', op: 'eq', value: 'Rack empty slot' }],
      keyword: '',
    });
  });

  it('still parses a QUOTED FIELD NAME, which starts AT the quote', () => {
    // The guard is `start < i < end`, strict on both sides, precisely so this
    // keeps working. A `<=` here would break every multi-word field name.
    //
    // The field comes back as the lower-cased RAW token, spaces intact — with no
    // synonym map, resolveFieldName finds no 'os normalized' key in
    // STATIC_FIELD_MAPPINGS and falls back to the token. That is correct and not
    // worth a mapping entry: buildFilters hands this to findFieldEntry, which
    // matches display_name case-insensitively ('OS Normalized' -> 'os
    // normalized'), so it resolves downstream. What this test pins is that the
    // quoted name survived as ONE token rather than being split.
    expect(parseSearchQuery('"OS Normalized" = Windows')).toEqual({
      filters: [{ field: 'os normalized', op: 'eq', value: 'Windows' }],
      keyword: '',
    });
  });

  it('still parses a real any-of list outside quotes', () => {
    // The fix must not disarm pass 2 where it is correct.
    expect(parseSearchQuery('category any Server,Network')).toEqual({
      filters: [{ field: 'category', op: 'any', value: ['Server', 'Network'] }],
      keyword: '',
    });
  });

  it('still parses a real is-empty filter outside quotes', () => {
    expect(parseSearchQuery('model empty')).toEqual({
      filters: [{ field: 'model', op: 'empty', value: null }],
      keyword: '',
    });
  });
});
