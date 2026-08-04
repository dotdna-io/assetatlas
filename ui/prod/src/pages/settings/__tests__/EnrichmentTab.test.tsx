// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { EnrichmentTab } from '../EnrichmentTab';
import { useEnrichmentDatasets, type EnrichmentDataset } from '@/api/settings';

// Mock only the datasets query so we can drive the 0 -> 1 transition
// synchronously via rerender. The modal's other hooks
// (useUploadEnrichmentDataset / useApplyEnrichmentDataset) stay real.
vi.mock('@/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/settings')>();
  return { ...actual, useEnrichmentDatasets: vi.fn() };
});
const mockUseDatasets = useEnrichmentDatasets as ReturnType<typeof vi.fn>;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

const SAMPLE_DATASET: EnrichmentDataset = {
  id: 'ds-1',
  name: 'aa',
  status: 'active',
  match_field: 'hostname',
  match_type: 'exact',
  target_field: 'name',
  row_count: 50,
  matched_count: null,
  uploaded_by: 'admin',
  created_at: '2026-06-03T00:00:00Z',
};

describe('EnrichmentTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Regression guard for the "modal resets after first upload" bug.
  //
  // A successful upload invalidates the ['enrichment'] queries (see
  // useUploadEnrichmentDataset), so the datasets list refetches and grows
  // 0 -> 1. That flips EnrichmentTab from its empty-state branch to its table
  // branch. The UploadModal must be rendered ONCE in a stable position — if it
  // is duplicated inside each branch, React remounts it on this transition and
  // wipes its post-upload success/preview view back to a blank form (exactly
  // what the user saw on their first upload).
  it('keeps the upload modal mounted (state intact) when the first dataset appears', () => {
    const qc = makeQueryClient();
    // Empty field catalog so the modal's useTagRuleFields() stays fresh (5-min
    // staleTime) and doesn't fire a stray fetch — this test never opens the
    // match-field dropdown.
    qc.setQueryData(['tag-rules', 'fields'], { entries: [] });

    mockUseDatasets.mockReturnValue({ data: { datasets: [] }, isLoading: false });

    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <EnrichmentTab />
      </QueryClientProvider>,
    );

    // Empty state → open the modal and type a recognizable value into it.
    fireEvent.click(screen.getByRole('button', { name: /Upload CSV/i }));
    const nameInput = screen.getByPlaceholderText('e.g. Location Mapping');
    fireEvent.change(nameInput, { target: { value: 'My Dataset' } });
    expect(nameInput).toHaveValue('My Dataset');

    // Datasets 0 -> 1: EnrichmentTab flips from the empty branch to the table.
    mockUseDatasets.mockReturnValue({
      data: { datasets: [SAMPLE_DATASET] },
      isLoading: false,
    });
    rerender(
      <QueryClientProvider client={qc}>
        <EnrichmentTab />
      </QueryClientProvider>,
    );

    // The table branch is genuinely rendered (empty state gone, row shown).
    expect(screen.queryByText(/No enrichment datasets configured/i)).toBeNull();
    expect(screen.getByText('aa')).toBeInTheDocument();
    // ...and the modal is still the SAME instance: the typed value survived.
    // Before the fix this transition remounted the modal and reset it to ''.
    expect(screen.getByPlaceholderText('e.g. Location Mapping')).toHaveValue(
      'My Dataset',
    );
  });
});
