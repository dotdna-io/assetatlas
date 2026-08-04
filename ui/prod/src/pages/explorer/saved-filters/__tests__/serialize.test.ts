// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { initialExplorerState } from '../../explorer-state';
import { serializeView, buildApplyAction, layoutDiffers, needsShareFlip, buildShareUrl } from '../serialize';
import type { SavedFilterPayload } from '@/api/saved-filters';

// A partial/legacy snapshot the backend (opaque JSONB, no structural
// validation) could return: no captured `columns`/`pivot` layout.
const PARTIAL_PAYLOAD = {
  version: 1,
  searchQuery: 'Category = Server',
  columnFilters: {},
  dateFilters: {},
  sort: { column: 'static:Hostname', direction: 'asc' },
} as unknown as SavedFilterPayload;

describe('serializeView', () => {
  it('captures the full view snapshot from state', () => {
    const state = {
      ...initialExplorerState,
      searchQuery: 'Category = Server',
      columnFilters: { 'static:Category': ['Server'] },
      dateFilters: { 'core:Last Seen': 30 },
      sortColumn: 'static:Hostname',
      sortDirection: 'desc' as const,
      columnWidths: { 'static:Hostname': 240 },
    };
    const p = serializeView(state);
    expect(p.version).toBe(1);
    expect(p.searchQuery).toBe('Category = Server');
    expect(p.columnFilters).toEqual({ 'static:Category': ['Server'] });
    expect(p.dateFilters).toEqual({ 'core:Last Seen': 30 });
    expect(p.sort).toEqual({ column: 'static:Hostname', direction: 'desc' });
    expect(p.columns.order).toEqual(state.columnOrder);
    expect(p.columns.visible).toEqual(state.visibleColumns);
    expect(p.columns.widths).toEqual({ 'static:Hostname': 240 });
    expect(p.pivot.mode).toBe(false);
    expect(p.pivot.config).toEqual(state.pivotConfig);
  });
});

describe('buildApplyAction', () => {
  it('re-derives structured filters from searchQuery (parity with URL hydration)', () => {
    const p = serializeView({ ...initialExplorerState, searchQuery: 'Category = Server' });
    const action = buildApplyAction(p, false);
    expect(action.type).toBe('APPLY_SAVED_FILTER');
    if (action.type !== 'APPLY_SAVED_FILTER') throw new Error('wrong action');
    expect(action.searchQuery).toBe('Category = Server');
    expect(action.structuredFilters.length).toBeGreaterThan(0);
    expect(action.layout).toBeNull(); // includeLayout=false
  });

  it('includes layout when includeLayout=true', () => {
    const p = serializeView(initialExplorerState);
    const action = buildApplyAction(p, true);
    if (action.type !== 'APPLY_SAVED_FILTER') throw new Error('wrong action');
    expect(action.layout).not.toBeNull();
    expect(action.layout?.columnOrder).toEqual(initialExplorerState.columnOrder);
  });

  it('degrades to a filters-only apply when the payload has no captured layout', () => {
    // A partial/legacy payload must not crash the Explorer on load.
    expect(() => buildApplyAction(PARTIAL_PAYLOAD, true)).not.toThrow();
    const action = buildApplyAction(PARTIAL_PAYLOAD, true);
    if (action.type !== 'APPLY_SAVED_FILTER') throw new Error('wrong action');
    expect(action.layout).toBeNull();
    expect(action.structuredFilters.length).toBeGreaterThan(0);
  });
});

describe('needsShareFlip', () => {
  // A private filter's link is owner-only (backend get_for_user), so sharing it
  // must first flip visibility to 'link'; already-shareable filters must NOT be
  // re-written — that avoids a redundant PUT and preserves an 'org' library filter.
  it('is true for a private filter (must grant link access before the link works)', () => {
    expect(needsShareFlip('private')).toBe(true);
  });

  it('is false for a link filter (already shareable)', () => {
    expect(needsShareFlip('link')).toBe(false);
  });

  it('is false for an org filter (already readable via link)', () => {
    expect(needsShareFlip('org')).toBe(false);
  });
});

describe('buildShareUrl', () => {
  it('builds a ?view=<id> link on the given origin + path (respects app base path)', () => {
    expect(buildShareUrl('abc123', 'https://app.example.com', '/explorer')).toBe(
      'https://app.example.com/explorer?view=abc123',
    );
  });
});

describe('layoutDiffers', () => {
  it('is false when the snapshot layout matches current state', () => {
    const p = serializeView(initialExplorerState);
    expect(layoutDiffers(initialExplorerState, p)).toBe(false);
  });

  it('is true when visible columns differ', () => {
    const p = serializeView({
      ...initialExplorerState,
      visibleColumns: { 'static:Hostname': false },
    });
    expect(layoutDiffers(initialExplorerState, p)).toBe(true);
  });

  it('is true when only column widths differ (so saved widths are not silently dropped)', () => {
    const p = serializeView({
      ...initialExplorerState,
      columnWidths: { 'static:Hostname': 999 },
    });
    expect(layoutDiffers(initialExplorerState, p)).toBe(true);
  });

  it('reports no difference (no crash) for a partial/legacy payload missing columns/pivot', () => {
    expect(() => layoutDiffers(initialExplorerState, PARTIAL_PAYLOAD)).not.toThrow();
    expect(layoutDiffers(initialExplorerState, PARTIAL_PAYLOAD)).toBe(false);
  });
});
