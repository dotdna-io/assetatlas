// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import {
  ASSET_LABEL_COLUMN_KEY,
  ASSET_TABLE_DEFAULT_COLUMNS,
} from '@/constants/assetTableColumns';
import { DEFAULT_COLUMNS, toAssetFilterOp } from '../ReportTable';

// ChartEditor imports DEFAULT_COLUMNS from ReportTable to seed new Data Table
// charts — the export must stay, and it must BE the shared asset-table list
// (#1153), not a third private copy.
describe('ReportTable DEFAULT_COLUMNS re-export', () => {
  it('is the shared asset-table default list', () => {
    expect(DEFAULT_COLUMNS).toBe(ASSET_TABLE_DEFAULT_COLUMNS);
  });

  it('starts with the pinned Asset Label column', () => {
    expect(DEFAULT_COLUMNS[0]).toBe(ASSET_LABEL_COLUMN_KEY);
  });
});

/**
 * Guards the operator translation between the chart wizard's filter form
 * (what FilterRow stores) and the asset list endpoint's AssetFilter.op
 * vocabulary (src/api/models/requests.py). Without this map, the only
 * operator that accidentally validates against AssetFilter is "contains";
 * every other filter on a Data Table chart returns 400.
 */
describe('toAssetFilterOp', () => {
  it.each([
    ['equals', 'eq'],
    ['not_equals', 'neq'],
    ['contains', 'contains'],
    ['not_contains', 'ncontains'],
    ['starts_with', 'starts'],
    ['ends_with', 'ends'],
    ['is_empty', 'empty'],
    ['is_not_empty', 'notempty'],
    ['greater_than', 'gt'],
    ['greater_than_or_equal', 'gte'],
    ['less_than', 'lt'],
    ['less_than_or_equal', 'lte'],
    ['any_of', 'any'],
  ])('translates underscore form %s -> AssetFilter op %s', (input, expected) => {
    expect(toAssetFilterOp(input)).toBe(expected);
  });

  it.each([
    ['not equals', 'neq'],
    ['starts with', 'starts'],
    ['is empty', 'empty'],
    ['is not empty', 'notempty'],
    ['greater than', 'gt'],
    ['less than or equal', 'lte'],
    ['any of', 'any'],
  ])('translates spaced form %s -> %s', (input, expected) => {
    expect(toAssetFilterOp(input)).toBe(expected);
  });

  it('passes unknown operators through unchanged', () => {
    expect(toAssetFilterOp('weird_op')).toBe('weird_op');
  });
});
