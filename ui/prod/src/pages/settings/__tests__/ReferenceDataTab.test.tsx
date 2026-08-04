// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { ReferenceDataTab } from '../ReferenceDataTab';
import { apiClient } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), upload: vi.fn() },
}));

const SAMPLE_STATUS = {
  current_bundle_version: '2026.17.1',
  last_applied_at: '2026-04-28T01:00:00Z',
  datasets: [
    { name: 'mac_vendor_ranges', rows_after: 51559, rows_before: 0, applied: true },
    { name: 'os_eol_data', rows_after: 7234, rows_before: 0, applied: true },
  ],
  history: [],
};

const SAMPLE_CHECK_AVAILABLE = {
  available: true,
  upstream_version: '2026.17.2',
  current_version: '2026.17.1',
  generated_at: '2026-04-28T03:00:00Z',
  app_compat_min: '2026.4.0',
  datasets: [
    { name: 'mac_vendor_ranges', row_count: 52000, source: 'ieee+wireshark' },
    { name: 'os_eol_data', row_count: 7300, source: 'endoflife.date' },
  ],
};

function renderTab() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ReferenceDataTab />
    </QueryClientProvider>,
  );
}

describe('ReferenceDataTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue(SAMPLE_STATUS);
  });

  it('renders current bundle version and dataset row counts', async () => {
    renderTab();
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith('/reference-data/status', { params: undefined }),
    );
    expect(await screen.findByText('2026.17.1')).toBeInTheDocument();
    expect(screen.getByText('mac_vendor_ranges')).toBeInTheDocument();
    expect(screen.getByText('51559')).toBeInTheDocument();
  });

  it('shows confirmation panel after Check, only applies after Apply update', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce(SAMPLE_CHECK_AVAILABLE);
    renderTab();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /check for updates/i }));

    // Panel appears with the upstream version + dataset summary
    await waitFor(() => expect(screen.getByText(/Update available/i)).toBeInTheDocument());
    expect(screen.getByText('2026.17.2')).toBeInTheDocument();
    expect(screen.getByText(/52,000/)).toBeInTheDocument();

    // /check was called, /apply-from-upstream was NOT called yet
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expect(apiClient.post).toHaveBeenCalledWith('/reference-data/check');

    // Now click Apply update
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      bundle_version: '2026.17.2',
      source: 'github',
      status: 'success',
      datasets: [
        { name: 'mac_vendor_ranges', rows_before: 51559, rows_after: 52000, applied: true },
        { name: 'os_eol_data', rows_before: 7234, rows_after: 7300, applied: true },
      ],
    });
    await user.click(screen.getByRole('button', { name: /apply update/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/reference-data/apply-from-upstream', {
        version: '2026.17.2',
      }),
    );
  });

  it('shows a friendly message (not a raw error) when upstream is unavailable', async () => {
    // The backend returns 200 with { status: 'upstream_unavailable' } rather than
    // a 502, so the mutation resolves and the onSuccess branch renders the message
    // inline — the original bug surfaced a raw "Request failed ... (502)" via onError.
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      status: 'upstream_unavailable',
      message: 'GitHub API unreachable (404)',
    });
    renderTab();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /check for updates/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/Upstream unavailable: GitHub API unreachable \(404\)/),
      ).toBeInTheDocument(),
    );
    // No "Update available" confirmation panel for an unavailable check.
    expect(screen.queryByText(/Update available/i)).toBeNull();
  });
});
