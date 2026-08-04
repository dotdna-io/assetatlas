// SPDX-License-Identifier: AGPL-3.0-or-later
// Shared default column list for every ExplorerTable surface (Explorer,
// Data Source Details, Reporting data table) — issue #1153. The three pages
// used to keep private copies that drifted apart; this module is the single
// definition. Static keys use the URL-encoded display-name convention that
// the assets API emits as row keys (see types/field-id.ts).

/** The canonical row identifier (COALESCE of override/hostname/fqdn/mac/sn/ip
 *  — src/modules/fields/catalog.py "asset_label"). Locked as the first column
 *  of every asset table: excluded from drag-reordering and un-hideable, but
 *  NOT frozen — it scrolls horizontally like any other column. */
export const ASSET_LABEL_COLUMN_KEY = 'virtual:asset_label';

/** Default columns for all three asset tables, in render order. Asset Label
 *  is pinned first; Hostname stays even though asset_label coalesces hostname
 *  first (maintainer decision on #1153). */
export const ASSET_TABLE_DEFAULT_COLUMNS: string[] = [
  ASSET_LABEL_COLUMN_KEY,
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
];

/**
 * Defensive normalization for any column order loaded from storage or a
 * saved config: dedupe, then force the pinned key to the front. With
 * `ensurePinned` (default) a missing pinned key is prepended — used where the
 * order manages the full column universe (Explorer / Data Source Details
 * persistence), so stale or hand-edited storage can't break the invariant.
 * Pass `ensurePinned: false` for explicit column selections (saved chart
 * configs feeding ReportTable), where an absent pin is a deliberate choice
 * and only its position is enforced.
 */
export function normalizeColumnOrder(
  order: string[],
  { ensurePinned = true }: { ensurePinned?: boolean } = {},
): string[] {
  const seen = new Set<string>();
  const rest: string[] = [];
  let hasPin = false;
  for (const key of order) {
    if (seen.has(key)) continue;
    seen.add(key);
    if (key === ASSET_LABEL_COLUMN_KEY) {
      hasPin = true;
    } else {
      rest.push(key);
    }
  }
  if (!hasPin && !ensurePinned) return rest;
  return [ASSET_LABEL_COLUMN_KEY, ...rest];
}

/** Force the pinned column visible in a loaded visibility map. */
export function normalizeVisibleColumns(
  visible: Record<string, boolean>,
): Record<string, boolean> {
  return { ...visible, [ASSET_LABEL_COLUMN_KEY]: true };
}

// ---------------------------------------------------------------------------
// Versioned localStorage persistence (Explorer _v4, Data Source Details _v1).
// Each page owns its key namespace; the load/save mechanics live here so the
// two can't drift. Keys are bumped when the default set changes so stored
// configs are ignored and existing users pick up the new defaults.
// ---------------------------------------------------------------------------

export interface ColumnConfigStorageKeys {
  order: string;
  visible: string;
  widths: string;
}

export interface AssetTableColumnConfig {
  columnOrder: string[];
  visibleColumns: Record<string, boolean>;
  columnWidths: Record<string, number>;
}

export function loadAssetTableColumnConfig(
  keys: ColumnConfigStorageKeys,
): AssetTableColumnConfig {
  let columnOrder: string[] = ASSET_TABLE_DEFAULT_COLUMNS;
  let visibleColumns: Record<string, boolean> = Object.fromEntries(
    ASSET_TABLE_DEFAULT_COLUMNS.map((col) => [col, true]),
  );
  let columnWidths: Record<string, number> = {};

  try {
    const storedOrder = localStorage.getItem(keys.order);
    if (storedOrder) {
      const parsed = JSON.parse(storedOrder);
      if (Array.isArray(parsed) && parsed.length > 0) {
        columnOrder = parsed;
      }
    }
  } catch {
    // ignore
  }

  try {
    const storedVisible = localStorage.getItem(keys.visible);
    if (storedVisible) {
      const parsed = JSON.parse(storedVisible);
      if (parsed && typeof parsed === 'object') {
        visibleColumns = parsed;
      }
    }
  } catch {
    // ignore
  }

  try {
    const storedWidths = localStorage.getItem(keys.widths);
    if (storedWidths) {
      const parsed = JSON.parse(storedWidths);
      if (parsed && typeof parsed === 'object') {
        columnWidths = parsed;
      }
    }
  } catch {
    // ignore
  }

  return {
    columnOrder: normalizeColumnOrder(columnOrder),
    visibleColumns: normalizeVisibleColumns(visibleColumns),
    columnWidths,
  };
}

export function saveAssetTableColumnConfig(
  keys: ColumnConfigStorageKeys,
  config: AssetTableColumnConfig,
): void {
  try {
    localStorage.setItem(keys.order, JSON.stringify(config.columnOrder));
    localStorage.setItem(keys.visible, JSON.stringify(config.visibleColumns));
    localStorage.setItem(keys.widths, JSON.stringify(config.columnWidths));
  } catch {
    // ignore — private browsing / storage full
  }
}
