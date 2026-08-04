// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Explorer drilldown from an alert, as an ordinary search filter.
 *
 * The scope used to ride its own request param and render a bespoke pill, because
 * no catalog field held an alert id. The `Alert` field changes that, and these
 * tests hold what the change buys:
 *  - the token lives in reducer state, so serializeView() persists it into a
 *    saved filter — the URL-only scope could not be saved at all;
 *  - it rides in filters[], so export and bulk actions inherit it with no
 *    special-casing and no second channel for the two to disagree through;
 *  - it renders in the search box, where every other filter renders.
 * And what it must not break: the field is FILTER-ONLY, so it must reach the
 * search bar but never the column picker — projecting it runs a correlated
 * subquery per asset row (>180s against the dev DB's 73,740 assets).
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';

import ExplorerPage from '../ExplorerPage';

// vi.hoisted so the mock factories below (which are hoisted above this file's
// consts) can close over the same arrays the tests read.
const captured = vi.hoisted(() => ({
  searchBar: [] as any[],
  columnSelector: [] as any[],
}));

vi.mock('@/api/assets', () => ({
  useAssets: vi.fn(),
  useExplorerFields: vi.fn(),
  useSearchSynonyms: vi.fn(() => ({ data: undefined })),
  assetsApi: { list: vi.fn(), aggregate: vi.fn() },
}));
vi.mock('@/api/saved-filters', () => ({
  useSavedFilters: vi.fn(() => ({ data: { mine: [], library: [] }, isLoading: false, isError: false })),
  useCreateSavedFilter: vi.fn(() => ({ mutate: vi.fn() })),
  useUpdateSavedFilter: vi.fn(() => ({ mutate: vi.fn() })),
  useDeleteSavedFilter: vi.fn(() => ({ mutate: vi.fn() })),
  savedFiltersApi: { get: vi.fn() },
}));
vi.mock('@/auth', () => ({ useAuth: () => ({ isAdmin: true }) }));
vi.mock('@/hooks', () => ({ useDocumentTitle: () => undefined }));
// Renders headerActions so BulkActionsMenu actually mounts — the real
// ExplorerTable renders it (ExplorerTable.tsx:386), and a stub that drops it
// silently disarms every assertion about the controls it carries.
vi.mock('../explorer/table/ExplorerTable', () => ({
  ExplorerTable: ({ headerActions }: { headerActions?: ReactNode }) => (
    <div>{headerActions}</div>
  ),
}));
vi.mock('../explorer/search/SearchBar', () => ({
  SearchBar: (props: any) => {
    captured.searchBar.push(props);
    return <div data-testid="searchbar" />;
  },
}));
vi.mock('../explorer/table/ColumnSelector', () => ({
  ColumnSelector: (props: any) => {
    captured.columnSelector.push(props);
    return <div />;
  },
}));

import { useAssets, useExplorerFields } from '@/api/assets';

const ALERT = '948e3f69-818b-4649-8f77-362048686475';
const TOKEN = 'Alert = "Data Source Stale"';

const mockUseAssets = useAssets as unknown as ReturnType<typeof vi.fn>;
const mockUseFields = useExplorerFields as unknown as ReturnType<typeof vi.fn>;

const HOSTNAME_ENTRY = {
  display_name: 'Hostname',
  field_id: { kind: 'static', name: 'Hostname' },
  encoded_key: 'static:Hostname',
  category: 'target',
  data_type: 'string',
  operator_specs: [],
  is_array: false,
  is_grouped: false,
  member_sources: null,
  filter_only: false,
};
const ALERT_ENTRY = {
  ...HOSTNAME_ENTRY,
  display_name: 'Alert',
  field_id: { kind: 'virtual', name: 'alert' },
  encoded_key: 'virtual:alert',
  category: 'virtual',
  filter_only: true,
};

/** MemoryRouter keeps history in memory, so window.location never moves — read
 *  the router's own location or the URL assertions below silently pass on ''. */
function LocationProbe() {
  return <div data-testid="loc">{useLocation().search}</div>;
}
const currentSearch = () => screen.getByTestId('loc').textContent ?? '';

function renderAt(search: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/explorer${search}`]}>
        <LocationProbe />
        <Routes>
          <Route path="/explorer" element={<ExplorerPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastAssetParams = () => mockUseAssets.mock.calls.at(-1)?.[0];
const lastSearchBarProps = () => captured.searchBar.at(-1);
const sentFilters = () => JSON.parse(lastAssetParams()?.filters ?? '[]');

beforeEach(() => {
  vi.clearAllMocks();
  captured.searchBar.length = 0;
  captured.columnSelector.length = 0;
  mockUseFields.mockReturnValue({ data: { entries: [HOSTNAME_ENTRY, ALERT_ENTRY] } });
  mockUseAssets.mockReturnValue({
    data: { assets: [], pagination: { total: 3, pages: 1, page: 1, limit: 50 } },
    isLoading: false,
    isFetching: false,
    error: null,
  });
  // jsdom implements neither; the export's downloadCSV uses both.
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => 'blob:x');
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
});

describe('Explorer alert filter', () => {
  it('opts into filter-only fields, without which Alert silently disappears from fieldMap', () => {
    // useExplorerFields(true) is the ONLY thing that keeps `Alert` in fieldMap.
    // Lose the `true` (e.g. an "unused param" cleanup) and findFieldEntry
    // returns null for it, buildFilters silently drops the filter, and the
    // Explorer renders the WHOLE FLEET while the search box still reads
    // `Alert = "..."` — every other test here mocks useExplorerFields, so
    // none of them would notice.
    renderAt('');
    expect(mockUseFields).toHaveBeenCalledWith(true);
  });

  it('shows the Alert token in the search box', () => {
    renderAt(`?search=${encodeURIComponent(TOKEN)}`);
    expect(lastSearchBarProps().value).toBe(TOKEN);
  });

  it('sends the alert as a filter, never as an alert_id param', async () => {
    renderAt(`?search=${encodeURIComponent(TOKEN)}`);
    await waitFor(() => expect(sentFilters()).toHaveLength(1));
    expect(lastAssetParams()).not.toHaveProperty('alert_id');
    expect(sentFilters()[0]).toMatchObject({
      field_id: { kind: 'virtual', name: 'alert' },
      op: 'eq',
      value: 'Data Source Stale',
    });
  });

  it('translates a legacy ?alert_id= bookmark into an Alert token', async () => {
    // Links shipped before this change are still in bookmarks and open tabs.
    // Translating them costs one token, and only because the field accepts the
    // id spelling too.
    renderAt(`?alert_id=${ALERT}`);
    await waitFor(() => expect(lastSearchBarProps().value).toBe(`Alert = ${ALERT}`));
    expect(sentFilters()[0]).toMatchObject({
      field_id: { kind: 'virtual', name: 'alert' },
      value: ALERT,
    });
    // The state→URL sync rebuilds the query string and no longer re-adds it.
    await waitFor(() => expect(currentSearch()).not.toContain('alert_id'));
  });

  it('no longer renders the standalone alert pill', () => {
    renderAt(`?search=${encodeURIComponent(TOKEN)}`);
    expect(
      screen.queryByRole('button', { name: /clear alert filter/i }),
    ).not.toBeInTheDocument();
  });

  it('offers the Alert field to the search bar', () => {
    renderAt('');
    const names = lastSearchBarProps().fields.map((f: any) => f.display_name);
    expect(names).toContain('Alert');
  });

  it('keeps filter-only fields out of the column picker', () => {
    renderAt('');
    const names = captured.columnSelector
      .at(-1)
      .fields.entries.map((f: any) => f.display_name);
    expect(names).toContain('Hostname');
    expect(names).not.toContain('Alert');
  });

  it('leaves bulk actions and export enabled under an alert filter', () => {
    // #1011's win, preserved: both resolve their target set server-side from the
    // same filters[] the table uses, so there is nothing to disable.
    renderAt(`?search=${encodeURIComponent(TOKEN)}`);
    expect(screen.queryByTitle(/unavailable while an alert filter/i)).not.toBeInTheDocument();
    expect(screen.getByTitle('Export')).not.toBeDisabled();
    expect(screen.getByRole('button', { name: /bulk actions/i })).not.toBeDisabled();
  });
});
