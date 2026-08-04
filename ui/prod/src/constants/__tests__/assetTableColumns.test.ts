// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach } from 'vitest';

import {
  ASSET_LABEL_COLUMN_KEY,
  ASSET_TABLE_DEFAULT_COLUMNS,
  loadAssetTableColumnConfig,
  normalizeColumnOrder,
  normalizeVisibleColumns,
  saveAssetTableColumnConfig,
} from '../assetTableColumns';

// The shared default set is THE contract of #1153 — all three ExplorerTable
// surfaces (Explorer, Data Source Details, Reporting) render these defaults.
describe('ASSET_TABLE_DEFAULT_COLUMNS', () => {
  it('is exactly the maintainer-approved 16-column set, in order', () => {
    expect(ASSET_TABLE_DEFAULT_COLUMNS).toEqual([
      'virtual:asset_label',
      'static:Hostname',
      'static:FQDN',
      'static:IP%20Address',
      'static:MAC%20Address',
      'static:Serial%20Number',
      'static:Category',
      'static:Subcategory',
      'static:OS%20Normalized',
      'static:Manufacturer',
      'static:Model',
      'static:Data%20Source',
      'static:Confidence%20Score',
      'static:Owner',
      'static:First%20Seen',
      'static:Last%20Seen',
    ]);
  });

  it('pins Asset Label first', () => {
    expect(ASSET_LABEL_COLUMN_KEY).toBe('virtual:asset_label');
    expect(ASSET_TABLE_DEFAULT_COLUMNS[0]).toBe(ASSET_LABEL_COLUMN_KEY);
  });
});

describe('normalizeColumnOrder', () => {
  it('moves a misplaced pinned key to the front', () => {
    expect(
      normalizeColumnOrder(['static:Hostname', ASSET_LABEL_COLUMN_KEY, 'static:Model']),
    ).toEqual([ASSET_LABEL_COLUMN_KEY, 'static:Hostname', 'static:Model']);
  });

  it('prepends the pinned key when missing (default ensurePinned)', () => {
    expect(normalizeColumnOrder(['static:Hostname', 'static:Model'])).toEqual([
      ASSET_LABEL_COLUMN_KEY,
      'static:Hostname',
      'static:Model',
    ]);
  });

  it('leaves an absent pin out with ensurePinned: false (explicit chart configs)', () => {
    expect(
      normalizeColumnOrder(['static:Hostname', 'static:Model'], { ensurePinned: false }),
    ).toEqual(['static:Hostname', 'static:Model']);
  });

  it('still reorders a present pin with ensurePinned: false', () => {
    expect(
      normalizeColumnOrder(['static:Hostname', ASSET_LABEL_COLUMN_KEY], {
        ensurePinned: false,
      }),
    ).toEqual([ASSET_LABEL_COLUMN_KEY, 'static:Hostname']);
  });

  it('dedupes repeated keys, keeping first position', () => {
    expect(
      normalizeColumnOrder(['static:Hostname', 'static:Model', 'static:Hostname']),
    ).toEqual([ASSET_LABEL_COLUMN_KEY, 'static:Hostname', 'static:Model']);
  });
});

describe('normalizeVisibleColumns', () => {
  it('forces the pinned key visible and preserves other entries', () => {
    expect(
      normalizeVisibleColumns({ [ASSET_LABEL_COLUMN_KEY]: false, 'static:Model': false }),
    ).toEqual({ [ASSET_LABEL_COLUMN_KEY]: true, 'static:Model': false });
  });
});

describe('asset table column persistence', () => {
  const KEYS = { order: 'testOrder_v9', visible: 'testVisible_v9', widths: 'testWidths_v9' };

  beforeEach(() => {
    localStorage.clear();
  });

  it('returns the shared defaults when storage is empty', () => {
    const config = loadAssetTableColumnConfig(KEYS);
    expect(config.columnOrder).toEqual(ASSET_TABLE_DEFAULT_COLUMNS);
    expect(config.visibleColumns).toEqual(
      Object.fromEntries(ASSET_TABLE_DEFAULT_COLUMNS.map((c) => [c, true])),
    );
    expect(config.columnWidths).toEqual({});
  });

  it('round-trips a saved config', () => {
    const saved = {
      columnOrder: [ASSET_LABEL_COLUMN_KEY, 'static:Model', 'static:Hostname'],
      visibleColumns: { [ASSET_LABEL_COLUMN_KEY]: true, 'static:Model': true, 'static:Hostname': true },
      columnWidths: { 'static:Model': 220 },
    };
    saveAssetTableColumnConfig(KEYS, saved);
    expect(loadAssetTableColumnConfig(KEYS)).toEqual(saved);
  });

  it('normalizes stale storage: pin missing from order and hidden in the map', () => {
    localStorage.setItem(KEYS.order, JSON.stringify(['static:Hostname', 'static:Model']));
    localStorage.setItem(KEYS.visible, JSON.stringify({ [ASSET_LABEL_COLUMN_KEY]: false }));
    const config = loadAssetTableColumnConfig(KEYS);
    expect(config.columnOrder[0]).toBe(ASSET_LABEL_COLUMN_KEY);
    expect(config.visibleColumns[ASSET_LABEL_COLUMN_KEY]).toBe(true);
  });

  it('moves a mid-list pinned key from hand-edited storage to the front', () => {
    localStorage.setItem(
      KEYS.order,
      JSON.stringify(['static:Hostname', ASSET_LABEL_COLUMN_KEY, 'static:Model']),
    );
    expect(loadAssetTableColumnConfig(KEYS).columnOrder[0]).toBe(ASSET_LABEL_COLUMN_KEY);
  });

  it('falls back to defaults on corrupt JSON', () => {
    localStorage.setItem(KEYS.order, '{not json');
    localStorage.setItem(KEYS.visible, '[1,2');
    localStorage.setItem(KEYS.widths, 'nope');
    const config = loadAssetTableColumnConfig(KEYS);
    expect(config.columnOrder).toEqual(ASSET_TABLE_DEFAULT_COLUMNS);
  });
});
