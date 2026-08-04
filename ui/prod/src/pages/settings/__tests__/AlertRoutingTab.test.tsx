// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AlertRoutingTab } from '../AlertRoutingTab';
import { useRouting, useChannels, useSetCategoryRouting, ROUTING_CATEGORIES } from '../../../api/channels';
import type { AlertChannel, RouteRow } from '../../../api/channels';

// Mock the hooks only — same approach as UnifiedFieldsTab.test.tsx. The real
// ROUTING_CATEGORIES/types come through via importOriginal so the test stays
// honest about the shape Task 5 shipped.
vi.mock('../../../api/channels', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/channels')>();
  return {
    ...actual,
    useRouting: vi.fn(),
    useChannels: vi.fn(),
    useSetCategoryRouting: vi.fn(),
  };
});

const mockUseRouting = useRouting as ReturnType<typeof vi.fn>;
const mockUseChannels = useChannels as ReturnType<typeof vi.fn>;
const mockUseSetCategoryRouting = useSetCategoryRouting as ReturnType<typeof vi.fn>;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderTab() {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <AlertRoutingTab />
    </QueryClientProvider>,
  );
}

const CHANNELS: AlertChannel[] = [
  { id: 'c1', type: 'webhook', name: 'ops', enabled: true, config: {} },
  { id: 'c2', type: 'webhook', name: 'audit', enabled: true, config: {} },
];

const ROUTING: Record<string, RouteRow[]> = {
  security: [
    { channel_id: 'c1', channel_type: 'webhook', name: 'ops', enabled: true, min_severity: 'high' },
  ],
};

describe('AlertRoutingTab', () => {
  const mutate = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockUseRouting.mockReturnValue({ data: ROUTING, isLoading: false });
    mockUseChannels.mockReturnValue({ data: CHANNELS, isLoading: false });
    mockUseSetCategoryRouting.mockReturnValue({ mutate, isPending: false });
  });

  it('renders every routing category label', () => {
    renderTab();
    for (const cat of ROUTING_CATEGORIES) {
      expect(screen.getByText(cat.label)).toBeInTheDocument();
    }
  });

  it('renders AssetAtlas as a non-removable default in every category', () => {
    renderTab();
    expect(screen.getAllByText('AssetAtlas (in-app)')).toHaveLength(ROUTING_CATEGORIES.length);
    // Non-removable: no remove control for it anywhere in the document.
    expect(screen.queryByRole('button', { name: /remove assetatlas/i })).not.toBeInTheDocument();
  });

  it("shows the Security category's routed channel", () => {
    renderTab();
    // Scoped to a row-name <div> — 'ops' is also an unrelated <option> in the
    // "add channel" pickers of the four categories it isn't routed to yet.
    expect(screen.getByText('ops', { selector: 'div' })).toBeInTheDocument();
  });

  it('changing severity then saving calls setCategoryRouting with only the real channel', async () => {
    const user = userEvent.setup();
    renderTab();

    const severitySelect = screen.getByLabelText('ops minimum severity');
    await user.selectOptions(severitySelect, 'critical');

    // Security is the first rendered category section.
    const saveButtons = screen.getAllByRole('button', { name: /^save$/i });
    await user.click(saveButtons[0]);

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({
      category: 'security',
      channels: [{ channel_id: 'c1', min_severity: 'critical' }],
    });
  });
});
