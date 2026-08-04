// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ExplorerState, ExplorerAction } from '../explorer-state';
import { initialExplorerState } from '../explorer-state';
import { parseSearchQuery } from '../explorer-query-parser';
import type { SavedFilterVisibility, SavedFilterPayload } from '@/api/saved-filters';

/** Capture the current Explorer view into a persistable snapshot. */
export function serializeView(state: ExplorerState): SavedFilterPayload {
  return {
    version: 1,
    searchQuery: state.searchQuery,
    columnFilters: state.columnFilters,
    dateFilters: state.dateFilters,
    sort: { column: state.sortColumn, direction: state.sortDirection },
    columns: {
      order: state.columnOrder,
      visible: state.visibleColumns,
      widths: state.columnWidths,
    },
    pivot: { mode: state.pivotMode, config: state.pivotConfig },
  };
}

/**
 * Build the reducer action that applies a saved snapshot. Filters/sort/date
 * always apply; layout (columns + pivot) applies only when includeLayout.
 * Search filters are re-derived by parsing searchQuery, exactly like the
 * URL→state hydration path.
 */
export function buildApplyAction(
  payload: SavedFilterPayload,
  includeLayout: boolean,
): ExplorerAction {
  const parsed = payload.searchQuery
    ? parseSearchQuery(payload.searchQuery)
    : { filters: [], keyword: '' };
  return {
    type: 'APPLY_SAVED_FILTER',
    searchQuery: payload.searchQuery ?? '',
    keywordSearch: parsed.keyword,
    structuredFilters: parsed.filters,
    columnFilters: payload.columnFilters ?? {},
    dateFilters: payload.dateFilters ?? {},
    sortColumn: payload.sort?.column ?? initialExplorerState.sortColumn,
    sortDirection: payload.sort?.direction ?? initialExplorerState.sortDirection,
    // `columns`/`pivot` are typed as required, but `payload` is opaque JSON the
    // backend stores without structural validation — a partial/legacy snapshot
    // degrades to a filters-only apply rather than crashing the Explorer.
    layout:
      includeLayout && payload.columns && payload.pivot
        ? {
            columnOrder: payload.columns.order,
            visibleColumns: payload.columns.visible,
            columnWidths: payload.columns.widths,
            pivotMode: payload.pivot.mode,
            pivotConfig: payload.pivot.config,
          }
        : null,
  };
}

/**
 * Whether sharing this filter requires first granting link access. A private
 * filter is owner-only (backend `get_for_user`), so its `?view=` link only works
 * once visibility is flipped to 'link'. Already-shareable filters ('link'/'org')
 * need no write — returning false avoids a redundant PUT and preserves 'org'.
 */
export function needsShareFlip(visibility: SavedFilterVisibility): boolean {
  return visibility === 'private';
}

/** Build the shareable Explorer deep link for a saved filter (`?view=<id>`). */
export function buildShareUrl(id: string, origin: string, pathname: string): string {
  return `${origin}${pathname}?view=${id}`;
}

/** True when applying the snapshot would change the visible column/pivot layout. */
export function layoutDiffers(state: ExplorerState, payload: SavedFilterPayload): boolean {
  // A snapshot without a captured layout (a partial/legacy payload) can't change
  // the viewer's columns/pivot, so report no difference (and don't deref undefined).
  if (!payload.columns || !payload.pivot) return false;
  const current = JSON.stringify({
    order: state.columnOrder,
    visible: state.visibleColumns,
    widths: state.columnWidths,
    pivotMode: state.pivotMode,
    pivotConfig: state.pivotConfig,
  });
  const saved = JSON.stringify({
    order: payload.columns.order,
    visible: payload.columns.visible,
    widths: payload.columns.widths,
    pivotMode: payload.pivot.mode,
    pivotConfig: payload.pivot.config,
  });
  return current !== saved;
}
