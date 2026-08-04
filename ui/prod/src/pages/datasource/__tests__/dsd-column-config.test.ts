// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach } from 'vitest';

import {
  ASSET_LABEL_COLUMN_KEY,
  ASSET_TABLE_DEFAULT_COLUMNS,
  loadAssetTableColumnConfig,
  saveAssetTableColumnConfig,
} from '@/constants/assetTableColumns';
import { DSD_COLUMN_STORAGE_KEYS } from '../DataSourceDetailsPage';

// Data Source Details gained column persistence with #1153 — its own
// versioned namespace, same load/save mechanics as Explorer. A revisit
// remounts the page, so "survives a remount" == a fresh load after save.
describe('Data Source Details column persistence', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('uses its own _v1 key namespace', () => {
    expect(DSD_COLUMN_STORAGE_KEYS).toEqual({
      order: 'dsdColumnOrder_v1',
      visible: 'dsdVisibleColumns_v1',
      widths: 'dsdColumnWidths_v1',
    });
  });

  it('defaults to the shared 16-column set with the pin first', () => {
    const config = loadAssetTableColumnConfig(DSD_COLUMN_STORAGE_KEYS);
    expect(config.columnOrder).toEqual(ASSET_TABLE_DEFAULT_COLUMNS);
    expect(config.columnOrder[0]).toBe(ASSET_LABEL_COLUMN_KEY);
  });

  it('round-trips a column config across a remount', () => {
    const saved = {
      columnOrder: [ASSET_LABEL_COLUMN_KEY, 'static:MAC%20Address', 'static:Hostname'],
      visibleColumns: {
        [ASSET_LABEL_COLUMN_KEY]: true,
        'static:MAC%20Address': true,
        'static:Hostname': true,
      },
      columnWidths: { 'static:MAC%20Address': 210 },
    };
    saveAssetTableColumnConfig(DSD_COLUMN_STORAGE_KEYS, saved);

    // Second load = the page remounting on a later visit.
    expect(loadAssetTableColumnConfig(DSD_COLUMN_STORAGE_KEYS)).toEqual(saved);
  });

  it('normalizes stale storage back to pin-first-and-visible', () => {
    localStorage.setItem(
      DSD_COLUMN_STORAGE_KEYS.order,
      JSON.stringify(['static:Hostname', 'static:MAC%20Address']),
    );
    localStorage.setItem(
      DSD_COLUMN_STORAGE_KEYS.visible,
      JSON.stringify({ [ASSET_LABEL_COLUMN_KEY]: false }),
    );

    const config = loadAssetTableColumnConfig(DSD_COLUMN_STORAGE_KEYS);
    expect(config.columnOrder[0]).toBe(ASSET_LABEL_COLUMN_KEY);
    expect(config.visibleColumns[ASSET_LABEL_COLUMN_KEY]).toBe(true);
  });
});
