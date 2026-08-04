// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach } from 'vitest';

import {
  ASSET_LABEL_COLUMN_KEY,
  ASSET_TABLE_DEFAULT_COLUMNS,
} from '@/constants/assetTableColumns';
import {
  explorerReducer,
  initialExplorerState,
  loadColumnConfig,
  saveColumnConfig,
} from '../explorer-state';

// Explorer persists its column config under versioned keys. The _v3 -> _v4
// bump (#1153) is what makes existing users pick up the shared 16-column
// default set: stale _v3 data must be ignored, not migrated.
describe('explorer column persistence (_v4)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('ignores a stored _v3 config entirely — new defaults win', () => {
    localStorage.setItem(
      'explorerColumnOrder_v3',
      JSON.stringify(['static:Hostname', 'static:Last%20Seen']),
    );
    localStorage.setItem('explorerVisibleColumns_v3', JSON.stringify({ 'static:Hostname': true }));
    localStorage.setItem('columnWidths_v3', JSON.stringify({ 'static:Hostname': 999 }));

    const config = loadColumnConfig();
    expect(config.columnOrder).toEqual(ASSET_TABLE_DEFAULT_COLUMNS);
    expect(config.columnWidths).toEqual({});
  });

  it('writes only the _v4 keys', () => {
    saveColumnConfig({
      columnOrder: ASSET_TABLE_DEFAULT_COLUMNS,
      visibleColumns: Object.fromEntries(ASSET_TABLE_DEFAULT_COLUMNS.map((c) => [c, true])),
      columnWidths: { 'static:Model': 200 },
    });

    expect(localStorage.getItem('explorerColumnOrder_v4')).not.toBeNull();
    expect(localStorage.getItem('explorerVisibleColumns_v4')).not.toBeNull();
    expect(localStorage.getItem('columnWidths_v4')).not.toBeNull();
    expect(localStorage.getItem('explorerColumnOrder_v3')).toBeNull();
    expect(localStorage.getItem('explorerVisibleColumns_v3')).toBeNull();
    expect(localStorage.getItem('columnWidths_v3')).toBeNull();
  });

  it('round-trips a _v4 config, pin-normalized on load', () => {
    saveColumnConfig({
      columnOrder: ['static:Hostname', ASSET_LABEL_COLUMN_KEY],
      visibleColumns: { 'static:Hostname': true, [ASSET_LABEL_COLUMN_KEY]: false },
      columnWidths: { 'static:Hostname': 180 },
    });

    const config = loadColumnConfig();
    expect(config.columnOrder).toEqual([ASSET_LABEL_COLUMN_KEY, 'static:Hostname']);
    expect(config.visibleColumns[ASSET_LABEL_COLUMN_KEY]).toBe(true);
    expect(config.columnWidths).toEqual({ 'static:Hostname': 180 });
  });
});

// The pinned Asset Label invariant is enforced in the reducer so every path
// that mutates column order/visibility — drag reorder, selector apply, saved
// filters — keeps it first and visible.
describe('explorerReducer pin enforcement', () => {
  it('SET_COLUMN_ORDER forces the pinned key first', () => {
    const next = explorerReducer(initialExplorerState, {
      type: 'SET_COLUMN_ORDER',
      columnOrder: ['static:Hostname', ASSET_LABEL_COLUMN_KEY, 'static:Model'],
    });
    expect(next.columnOrder[0]).toBe(ASSET_LABEL_COLUMN_KEY);
  });

  it('SET_VISIBLE_COLUMNS forces the pinned key visible', () => {
    const next = explorerReducer(initialExplorerState, {
      type: 'SET_VISIBLE_COLUMNS',
      visibleColumns: { [ASSET_LABEL_COLUMN_KEY]: false, 'static:Hostname': true },
    });
    expect(next.visibleColumns[ASSET_LABEL_COLUMN_KEY]).toBe(true);
  });

  it('APPLY_SAVED_FILTER normalizes a stale saved layout', () => {
    const next = explorerReducer(initialExplorerState, {
      type: 'APPLY_SAVED_FILTER',
      searchQuery: '',
      keywordSearch: '',
      structuredFilters: [],
      columnFilters: {},
      dateFilters: {},
      sortColumn: 'static:Last%20Seen',
      sortDirection: 'desc',
      layout: {
        columnOrder: ['static:Hostname', 'static:Model'],
        visibleColumns: { 'static:Hostname': true, 'static:Model': true },
        columnWidths: {},
        pivotMode: false,
        pivotConfig: initialExplorerState.pivotConfig,
      },
    });
    expect(next.columnOrder[0]).toBe(ASSET_LABEL_COLUMN_KEY);
    expect(next.visibleColumns[ASSET_LABEL_COLUMN_KEY]).toBe(true);
  });
});
