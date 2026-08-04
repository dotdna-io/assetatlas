// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/unified-fields/__tests__/UnifiedFieldModalPreview.test.tsx
//
// Task 8: Live preview panel — TDD test suite.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { UnifiedFieldModal } from '../UnifiedFieldModal';
import {
  useUnifiedFields,
  useCreateUnifiedField,
  useUpdateUnifiedField,
  usePreviewUnifiedField,
} from '@/api/unifiedFields';
import { useExplorerFields } from '@/api/assets';
import type { UnifiedField, PreviewRow } from '@/api/unifiedFields';
import type { ExplorerFieldsResponse } from '@/api/assets';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/api/unifiedFields', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/unifiedFields')>();
  return {
    ...actual,
    useUnifiedFields: vi.fn(),
    useCreateUnifiedField: vi.fn(),
    useUpdateUnifiedField: vi.fn(),
    usePreviewUnifiedField: vi.fn(),
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
const mockUsePreviewUnifiedField = usePreviewUnifiedField as ReturnType<typeof vi.fn>;
const mockUseExplorerFields = useExplorerFields as ReturnType<typeof vi.fn>;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CATALOG: ExplorerFieldsResponse = {
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

// A valid unified field (name + function + 2 compatible sources)
const VALID_FIELD: UnifiedField = {
  id: 'uf-1',
  name: 'Device Hostname',
  function: 'coalesce',
  sources: ['static:Hostname', 'static:OS%20Name'],
  data_type: 'string',
  encoded_key: 'virtual:device_hostname',
};

const PREVIEW_ROWS: PreviewRow[] = [
  {
    asset_id: 'a1',
    hostname: 'server-01',
    source_values: {
      'static:Hostname': 'server-01',
      'static:OS%20Name': null,
    },
    result: 'server-01',
  },
  {
    asset_id: 'a2',
    hostname: 'workstation-42',
    source_values: {
      'static:Hostname': null,
      'static:OS%20Name': 'Windows 11',
    },
    result: 'Windows 11',
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('UnifiedFieldModal — live preview (Task 8)', () => {
  let mutateMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();

    mutateMock = vi.fn();

    mockUseCreateUnifiedField.mockReturnValue({ mutate: vi.fn(), isPending: false });
    mockUseUpdateUnifiedField.mockReturnValue({ mutate: vi.fn(), isPending: false });
    mockUseUnifiedFields.mockReturnValue({ data: { fields: [] }, isLoading: false });
    mockUseExplorerFields.mockReturnValue({ data: CATALOG });

    // Default: no preview data yet
    mockUsePreviewUnifiedField.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
      data: undefined,
    });
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('calls preview mutate after debounce when the draft is valid', () => {
    render(wrap(<UnifiedFieldModal initial={VALID_FIELD} onClose={vi.fn()} />));

    expect(mutateMock).not.toHaveBeenCalled();

    // Advance timers past the debounce window (350 ms used in component)
    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(mutateMock).toHaveBeenCalledTimes(1);

    const callArg = mutateMock.mock.calls[0][0];
    expect(callArg).toEqual({ function: 'coalesce', sources: ['static:Hostname', 'static:OS%20Name'] });
    expect(callArg).not.toHaveProperty('encoded_key');
    expect(callArg).not.toHaveProperty('data_type');
  });

  it('does NOT call preview mutate when the draft is invalid (fewer than 2 sources)', () => {
    // Field with only 1 source — validateDefinition will error
    const invalidField: UnifiedField = {
      ...VALID_FIELD,
      sources: ['static:Hostname'],
    };

    render(wrap(<UnifiedFieldModal initial={invalidField} onClose={vi.fn()} />));

    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(mutateMock).not.toHaveBeenCalled();
  });

  it('renders a preview table with hostname, source columns, and result', () => {
    mockUsePreviewUnifiedField.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
      data: { success: true, rows: PREVIEW_ROWS },
    });

    render(wrap(<UnifiedFieldModal initial={VALID_FIELD} onClose={vi.fn()} />));

    act(() => {
      vi.advanceTimersByTime(400);
    });

    // The fixed row-identifier column shows virtual:asset_label, so its header is
    // "Asset Label" — NOT "Hostname". That keeps it from reading as a duplicate
    // of the Hostname source column (which appears exactly once, from sources).
    expect(screen.getByRole('columnheader', { name: /asset label/i })).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader', { name: /^hostname$/i })).toHaveLength(1);

    // result column header
    expect(screen.getByRole('columnheader', { name: /result/i })).toBeInTheDocument();

    // row data — values may repeat across hostname, source, and result columns
    expect(screen.getAllByText('server-01').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('workstation-42').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Windows 11').length).toBeGreaterThanOrEqual(1);
  });

  it('shows a loading indicator while isPending is true', () => {
    mockUsePreviewUnifiedField.mockReturnValue({
      mutate: mutateMock,
      isPending: true,
      data: undefined,
    });

    render(wrap(<UnifiedFieldModal initial={VALID_FIELD} onClose={vi.fn()} />));

    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(screen.getByText(/loading preview/i)).toBeInTheDocument();
  });

  it('shows an empty state when rows array is empty', () => {
    mockUsePreviewUnifiedField.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
      data: { success: true, rows: [] },
    });

    render(wrap(<UnifiedFieldModal initial={VALID_FIELD} onClose={vi.fn()} />));

    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(screen.getByText(/no sample data/i)).toBeInTheDocument();
  });

  it('surfaces a preview error instead of silently hiding the panel', () => {
    mockUsePreviewUnifiedField.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
      isError: true,
      error: new Error('preview blew up'),
      data: undefined,
    });

    render(wrap(<UnifiedFieldModal initial={VALID_FIELD} onClose={vi.fn()} />));

    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(screen.getByText(/preview blew up/i)).toBeInTheDocument();
  });

  it('formats array (union) values as a "; "-joined list, not [object Object]', () => {
    const ARRAY_ROWS: PreviewRow[] = [
      {
        asset_id: 'a1',
        hostname: 'h1',
        source_values: {
          'static:Hostname': ['h1', 'h1-alt'],
          'static:OS%20Name': 'Windows',
        },
        result: ['h1', 'h1-alt', 'Windows'],
      },
    ];
    mockUsePreviewUnifiedField.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
      data: { success: true, rows: ARRAY_ROWS },
    });

    render(wrap(<UnifiedFieldModal initial={VALID_FIELD} onClose={vi.fn()} />));

    act(() => {
      vi.advanceTimersByTime(400);
    });

    // Result column and a multi-value source cell both join with "; "
    expect(screen.getByText('h1; h1-alt; Windows')).toBeInTheDocument();
    expect(screen.getByText('h1; h1-alt')).toBeInTheDocument();
    expect(screen.queryByText('[object Object]')).toBeNull();
  });
});
