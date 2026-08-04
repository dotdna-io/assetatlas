// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useDashboardCoverageTrend } from '../dashboard';
import { apiClient } from '../client';

vi.mock('../client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), del: vi.fn() },
}));

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // eslint-disable-next-line react/display-name
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useDashboardCoverageTrend', () => {
  beforeEach(() => vi.clearAllMocks());

  it('GETs /dashboard/coverage-trend with metric + days', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      metric: 'srv_edr', range: '30d', granularity: 'daily',
      data: [{ label: 'Jun 01', added: 5, removed: 3 }],
    });

    const { result } = renderHook(
      () => useDashboardCoverageTrend('srv_edr', 30),
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiClient.get).toHaveBeenCalledWith(
      '/dashboard/coverage-trend', { params: { metric: 'srv_edr', days: 30 } },
    );
    expect(result.current.data?.data[0].added).toBe(5);
  });

  it('does not fetch when disabled', async () => {
    const { result } = renderHook(
      () => useDashboardCoverageTrend('srv_edr', 30, false),
      { wrapper: wrapper() },
    );
    // give react-query a tick; the query must stay idle
    await new Promise((r) => setTimeout(r, 0));
    expect(apiClient.get).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
  });
});
