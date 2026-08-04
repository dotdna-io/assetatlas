// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { UnifiedFieldsTab } from '../UnifiedFieldsTab';
import { useUnifiedFields, useDeleteUnifiedField } from '@/api/unifiedFields';
import type { UnifiedField } from '@/api/unifiedFields';

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

function wrap(ui: React.ReactElement) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      {ui}
    </QueryClientProvider>
  );
}

const SAMPLE_FIELD: UnifiedField = {
  id: 'uf-1',
  name: 'Location',
  function: 'coalesce',
  sources: ['field:a', 'field:b', 'field:c'],
  data_type: 'string',
  encoded_key: 'virtual:location',
};

describe('UnifiedFieldsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseDeleteUnifiedField.mockReturnValue({ mutate: vi.fn(), isPending: false });
  });

  it('renders a row for each unified field with name, function label, sources count, and data type', () => {
    mockUseUnifiedFields.mockReturnValue({
      data: { fields: [SAMPLE_FIELD] },
      isLoading: false,
    });

    render(wrap(<UnifiedFieldsTab />));

    expect(screen.getByText('Location')).toBeInTheDocument();
    // Function label: "Coalesce" (label for coalesce)
    expect(screen.getByText(/coalesce/i)).toBeInTheDocument();
    // Source count
    expect(screen.getByText('3 fields')).toBeInTheDocument();
    // Data type
    expect(screen.getByText('string')).toBeInTheDocument();
  });

  it('shows empty state hint when there are no unified fields', () => {
    mockUseUnifiedFields.mockReturnValue({
      data: { fields: [] },
      isLoading: false,
    });

    render(wrap(<UnifiedFieldsTab />));

    expect(screen.getByText(/No unified fields configured/i)).toBeInTheDocument();
  });

  it('opens the modal with "Add unified field" title when the Add button is clicked', () => {
    mockUseUnifiedFields.mockReturnValue({
      data: { fields: [SAMPLE_FIELD] },
      isLoading: false,
    });

    render(wrap(<UnifiedFieldsTab />));

    fireEvent.click(screen.getByRole('button', { name: /Add unified field/i }));

    expect(screen.getByText('Add unified field', { selector: 'h2' })).toBeInTheDocument();
  });

  it('opens the modal with "Edit unified field" title when the edit button is clicked', () => {
    mockUseUnifiedFields.mockReturnValue({
      data: { fields: [SAMPLE_FIELD] },
      isLoading: false,
    });

    render(wrap(<UnifiedFieldsTab />));

    fireEvent.click(screen.getByRole('button', { name: /edit location/i }));

    expect(screen.getByText('Edit unified field', { selector: 'h2' })).toBeInTheDocument();
  });

  it('shows the Add button in the empty state too', () => {
    mockUseUnifiedFields.mockReturnValue({
      data: { fields: [] },
      isLoading: false,
    });

    render(wrap(<UnifiedFieldsTab />));

    expect(screen.getByRole('button', { name: /Add unified field/i })).toBeInTheDocument();
  });

  it('renders an error message when the delete mutation fails', () => {
    mockUseUnifiedFields.mockReturnValue({
      data: { fields: [SAMPLE_FIELD] },
      isLoading: false,
    });

    // mutate immediately calls onError with the passed callback
    mockUseDeleteUnifiedField.mockReturnValue({
      mutate: (_id: string, { onError }: { onError: (e: Error) => void }) => {
        onError(new Error('server error'));
      },
      isPending: false,
    });

    render(wrap(<UnifiedFieldsTab />));

    // Open the confirm dialog via the row's Delete button (aria-label "Delete <name>")
    fireEvent.click(screen.getByRole('button', { name: /^delete location$/i }));
    // Confirm via the dialog's Delete button — its accessible name is exactly
    // "Delete" (the row button is now "Delete Location"), so this resolves uniquely.
    fireEvent.click(screen.getByRole('button', { name: /^delete$/i }));

    expect(screen.getByText(/Failed to delete "Location"/i)).toBeInTheDocument();
  });
});
