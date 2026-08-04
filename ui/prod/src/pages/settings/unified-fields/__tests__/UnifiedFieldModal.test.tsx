// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/unified-fields/__tests__/UnifiedFieldModal.test.tsx

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { UnifiedFieldModal } from '../UnifiedFieldModal';
import { useUnifiedFields, useCreateUnifiedField, useUpdateUnifiedField } from '@/api/unifiedFields';
import { useExplorerFields } from '@/api/assets';
import { ApiError } from '@/api/client';
import type { UnifiedField } from '@/api/unifiedFields';
import type { ExplorerFieldsResponse } from '@/api/assets';

vi.mock('@/api/unifiedFields', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/unifiedFields')>();
  return {
    ...actual,
    useUnifiedFields: vi.fn(),
    useCreateUnifiedField: vi.fn(),
    useUpdateUnifiedField: vi.fn(),
  };
});

vi.mock('@/api/assets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/assets')>();
  return {
    ...actual,
    useExplorerFields: vi.fn(),
  };
});

const mockUseUnifiedFields = useUnifiedFields as ReturnType<typeof vi.fn>;
const mockUseCreateUnifiedField = useCreateUnifiedField as ReturnType<typeof vi.fn>;
const mockUseUpdateUnifiedField = useUpdateUnifiedField as ReturnType<typeof vi.fn>;
const mockUseExplorerFields = useExplorerFields as ReturnType<typeof vi.fn>;

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

// Catalog entries for source keys used in tests
const CATALOG_DATE: ExplorerFieldsResponse = {
  entries: [
    {
      display_name: 'First Seen',
      field_id: { kind: 'static', name: 'First Seen' },
      encoded_key: 'static:First%20Seen',
      category: 'target',
      data_type: 'timestamp',
      operator_specs: [],
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
    {
      display_name: 'Last Seen',
      field_id: { kind: 'static', name: 'Last Seen' },
      encoded_key: 'static:Last%20Seen',
      category: 'target',
      data_type: 'timestamp',
      operator_specs: [],
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
  ],
};

const CATALOG_TEXT: ExplorerFieldsResponse = {
  entries: [
    {
      display_name: 'Hostname',
      field_id: { kind: 'static', name: 'Hostname' },
      encoded_key: 'static:Hostname',
      category: 'target',
      data_type: 'string',
      operator_specs: [],
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
    {
      display_name: 'OS Name',
      field_id: { kind: 'static', name: 'OS Name' },
      encoded_key: 'static:OS%20Name',
      category: 'target',
      data_type: 'string',
      operator_specs: [],
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
  ],
};

// Identity fields are STRING_ARRAY in the production catalog (catalog.py) — the
// case that exposed the "Save never enables" trap. Hostname + FQDN are both
// multi-value, so only `union` is a valid merge function.
const CATALOG_TEXT_ARRAY: ExplorerFieldsResponse = {
  entries: [
    {
      display_name: 'Hostname',
      field_id: { kind: 'static', name: 'Hostname' },
      encoded_key: 'static:Hostname',
      category: 'target',
      data_type: 'string_array',
      operator_specs: [],
      is_array: true,
      is_grouped: false,
      member_sources: null,
    },
    {
      display_name: 'FQDN',
      field_id: { kind: 'static', name: 'FQDN' },
      encoded_key: 'static:FQDN',
      category: 'target',
      data_type: 'string_array',
      operator_specs: [],
      is_array: true,
      is_grouped: false,
      member_sources: null,
    },
  ],
};

// Catalog mixing a real field with non-source entries the backend rejects as
// sources: a built-in virtual (confidence_level) and a saved unified field.
const CATALOG_WITH_NON_SOURCES: ExplorerFieldsResponse = {
  entries: [
    {
      display_name: 'Hostname',
      field_id: { kind: 'static', name: 'Hostname' },
      encoded_key: 'static:Hostname',
      category: 'target',
      data_type: 'string',
      operator_specs: [],
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
    {
      display_name: 'Confidence Level',
      field_id: { kind: 'virtual', name: 'confidence_level' },
      encoded_key: 'virtual:confidence_level',
      category: 'virtual',
      data_type: 'string',
      operator_specs: [],
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
    {
      // A previously-saved unified field. Its field_id is virtual:<slug> on the
      // wire; the frontend FieldId type models only confidence_level, so the
      // fixture uses a static field_id here (immaterial — the picker filters on
      // category, not field_id).
      display_name: 'Merged Lentity',
      field_id: { kind: 'static', name: 'Merged Lentity' },
      encoded_key: 'virtual:lentity',
      category: 'unified',
      data_type: 'string',
      operator_specs: [],
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
  ],
};

const DATE_FIELD: UnifiedField = {
  id: 'uf-date',
  name: 'Seen Window',
  function: 'max',
  sources: ['static:First%20Seen', 'static:Last%20Seen'],
  data_type: 'timestamp',
  encoded_key: 'virtual:seen_window',
};

const TEXT_FIELD: UnifiedField = {
  id: 'uf-text',
  name: 'Device Name',
  function: 'coalesce',
  sources: ['static:Hostname', 'static:OS%20Name'],
  data_type: 'string',
  encoded_key: 'virtual:device_name',
};

describe('UnifiedFieldModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseCreateUnifiedField.mockReturnValue({ mutate: vi.fn(), isPending: false });
    mockUseUpdateUnifiedField.mockReturnValue({ mutate: vi.fn(), isPending: false });
    mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });
  });

  describe('source-field picker', () => {
    it('adds two fields via the picker and renders both in order', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_DATE });
      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      // Open "Add field" and pick "First Seen"
      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'First Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'First Seen' }));

      // Open again and pick "Last Seen"
      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'Last Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'Last Seen' }));

      // Both appear in the sources list in order
      const items = screen.getAllByRole('listitem');
      expect(items[0]).toHaveTextContent('First Seen');
      expect(items[1]).toHaveTextContent('Last Seen');
    });

    it('moves a field down with the down button', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_DATE });
      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'First Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'First Seen' }));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'Last Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'Last Seen' }));

      // Move "First Seen" down
      const downButtons = screen.getAllByRole('button', { name: /move down/i });
      fireEvent.click(downButtons[0]);

      const items = screen.getAllByRole('listitem');
      expect(items[0]).toHaveTextContent('Last Seen');
      expect(items[1]).toHaveTextContent('First Seen');
    });

    it('moves a field up with the up button', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_DATE });
      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'First Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'First Seen' }));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'Last Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'Last Seen' }));

      // Move "Last Seen" up
      const upButtons = screen.getAllByRole('button', { name: /move up/i });
      fireEvent.click(upButtons[1]);

      const items = screen.getAllByRole('listitem');
      expect(items[0]).toHaveTextContent('Last Seen');
      expect(items[1]).toHaveTextContent('First Seen');
    });

    it('removes a field with the remove button', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_DATE });
      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'First Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'First Seen' }));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'Last Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'Last Seen' }));

      const removeButtons = screen.getAllByRole('button', { name: /remove/i });
      fireEvent.click(removeButtons[0]); // remove "First Seen"

      const items = screen.getAllByRole('listitem');
      expect(items).toHaveLength(1);
      expect(items[0]).toHaveTextContent('Last Seen');
    });

    it('skips duplicate additions', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_DATE });
      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'First Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'First Seen' }));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'First Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'First Seen' }));

      const items = screen.getAllByRole('listitem');
      expect(items).toHaveLength(1);
    });

    it('adding two date fields makes max and min appear in the function select', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_DATE });
      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'First Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'First Seen' }));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'Last Seen' }));
      fireEvent.click(screen.getByRole('option', { name: 'Last Seen' }));

      const select = screen.getByRole('combobox', { name: /function/i });
      const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);
      expect(options).toContain('max');
      expect(options).toContain('min');
    });
  });

  describe('function select — type-aware options', () => {
    it('offers max and min for two DATE sources', () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_DATE });
      mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });

      render(wrap(<UnifiedFieldModal initial={DATE_FIELD} onClose={vi.fn()} />));

      const select = screen.getByRole('combobox', { name: /function/i });
      const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);

      expect(options).toContain('max');
      expect(options).toContain('min');
    });

    it('does NOT offer max or min for two TEXT sources', () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_TEXT });
      mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });

      render(wrap(<UnifiedFieldModal initial={TEXT_FIELD} onClose={vi.fn()} />));

      const select = screen.getByRole('combobox', { name: /function/i });
      const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);

      expect(options).not.toContain('max');
      expect(options).not.toContain('min');
    });

    // Regression: multi-value (array) sources only support `union`. The dropdown
    // must offer ONLY union and auto-select it, instead of defaulting to the
    // invalid `coalesce` that left Save permanently disabled.
    it('offers only union (and auto-selects it) for multi-value array sources, keeping Save enabled', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_TEXT_ARRAY });
      mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });

      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      // Give it a name so the only thing under test is the function/source rule.
      fireEvent.change(screen.getByRole('textbox', { name: /name/i }), {
        target: { value: 'Device Name' },
      });

      // Add the two array-typed identity fields.
      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'Hostname' }));
      fireEvent.click(screen.getByRole('option', { name: 'Hostname' }));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'FQDN' }));
      fireEvent.click(screen.getByRole('option', { name: 'FQDN' }));

      const select = screen.getByRole('combobox', { name: /function/i }) as HTMLSelectElement;
      const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);
      expect(options).toEqual(['union']);
      expect(select.value).toBe('union');

      // No "single-value source fields" error, and Save is reachable.
      expect(screen.queryByText(/single-value source fields/i)).toBeNull();
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /save/i })).toBeEnabled();
      });
    });
  });

  describe('name collision validation', () => {
    it('shows a validation error and disables Save when the name collides with an existing field', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_TEXT });
      mockUseUnifiedFields.mockReturnValue({
        data: { fields: [TEXT_FIELD] },
        isLoading: false,
      });

      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      const nameInput = screen.getByRole('textbox', { name: /name/i });
      fireEvent.change(nameInput, { target: { value: 'Device Name' } });

      await waitFor(() => {
        expect(screen.getByText(/a field named "Device Name" already exists/i)).toBeInTheDocument();
      });

      const saveBtn = screen.getByRole('button', { name: /save/i });
      expect(saveBtn).toBeDisabled();
    });
  });

  describe('source picker — real fields only (D6)', () => {
    it('excludes virtual and unified catalog entries; offers only real fields', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_WITH_NON_SOURCES });
      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      fireEvent.click(screen.getByRole('button', { name: /add field/i }));
      await waitFor(() => screen.getByRole('option', { name: 'Hostname' }));

      // The one real field is offered…
      expect(screen.getByRole('option', { name: 'Hostname' })).toBeInTheDocument();
      // …but the built-in virtual and the saved unified field are not (the
      // backend rejects both as sources).
      expect(screen.queryByRole('option', { name: 'Confidence Level' })).toBeNull();
      expect(screen.queryByRole('option', { name: 'Merged Lentity' })).toBeNull();
    });
  });

  describe('submit error surfacing', () => {
    it('renders the backend error message when the create mutation fails', () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_TEXT });
      mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });
      // Simulate the state after a failed submit (e.g. a 409 name-collision
      // race the client-side check didn't catch). The server's friendly message
      // must reach the user, not be swallowed.
      mockUseCreateUnifiedField.mockReturnValue({
        mutate: vi.fn(),
        isPending: false,
        isError: true,
        error: new ApiError('Request failed: POST /fields/unified (409)', 409, {
          message: 'A field named "Lentity" already exists',
        }),
      });

      render(wrap(<UnifiedFieldModal onClose={vi.fn()} />));

      expect(
        screen.getByText('A field named "Lentity" already exists'),
      ).toBeInTheDocument();
    });
  });

  describe('orphan source handling', () => {
    it('flags a saved source that no longer resolves in the catalog and disables Save', async () => {
      mockUseExplorerFields.mockReturnValue({ data: CATALOG_TEXT });
      mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });
      // 'static:Deleted%20Field' is not in CATALOG_TEXT — simulates a source
      // field removed after the unified field was created.
      const ORPHAN_FIELD: UnifiedField = {
        id: 'uf-orphan',
        name: 'Orphan',
        function: 'coalesce',
        sources: ['static:Hostname', 'static:Deleted%20Field'],
        data_type: 'string',
        encoded_key: 'virtual:orphan',
      };

      render(wrap(<UnifiedFieldModal initial={ORPHAN_FIELD} onClose={vi.fn()} />));

      await waitFor(() => {
        expect(screen.getByText(/no longer available/i)).toBeInTheDocument();
      });
      expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
    });
  });
});
