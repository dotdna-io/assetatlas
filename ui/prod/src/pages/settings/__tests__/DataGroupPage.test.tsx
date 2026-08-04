// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, Outlet } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { DataGroupPage } from '../DataGroupPage';
import { UnifiedFieldsTab } from '../UnifiedFieldsTab';
import { useUnifiedFields, useDeleteUnifiedField } from '@/api/unifiedFields';

vi.mock('@/api/unifiedFields', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/unifiedFields')>();
  return {
    ...actual,
    useUnifiedFields: vi.fn(),
    useDeleteUnifiedField: vi.fn(),
  };
});

const mockUseUnifiedFields = useUnifiedFields as ReturnType<typeof vi.fn>;
const mockUseDeleteUnifiedField = useDeleteUnifiedField as ReturnType<typeof vi.fn>;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderDataGroup(initialPath: string, childElement: React.ReactElement = <Outlet />) {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/settings/data" element={<DataGroupPage />}>
            <Route path="unified-fields" element={childElement} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DataGroupPage – Unified Fields tab registration', () => {
  it('renders the "Unified Fields" tab link in the sub-tab bar', () => {
    renderDataGroup('/settings/data/unified-fields');

    expect(screen.getByRole('link', { name: 'Unified Fields' })).toBeInTheDocument();
  });

  it('places "Unified Fields" next to "Enrichment" in the tab bar', () => {
    renderDataGroup('/settings/data/unified-fields');

    const links = screen.getAllByRole('link').map((el) => el.textContent);
    const enrichmentIdx = links.indexOf('Enrichment');
    const unifiedIdx = links.indexOf('Unified Fields');
    expect(enrichmentIdx).toBeGreaterThanOrEqual(0);
    expect(unifiedIdx).toBeGreaterThanOrEqual(0);
    // Unified Fields appears immediately after Enrichment
    expect(unifiedIdx).toBe(enrichmentIdx + 1);
  });

  it('renders UnifiedFieldsTab when the unified-fields route is active', () => {
    mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });
    mockUseDeleteUnifiedField.mockReturnValue({ mutate: vi.fn(), isPending: false });

    renderDataGroup('/settings/data/unified-fields', <UnifiedFieldsTab />);

    expect(screen.getByText(/No unified fields configured/i)).toBeInTheDocument();
  });
});
