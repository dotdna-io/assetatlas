// SPDX-License-Identifier: AGPL-3.0-or-later
import { renderHook, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  useAlertRules, useCreateAlertRule, useDeleteAlertRule, useToggleAlertRule,
  useUpdateAnomalyThreshold, useAlertHistory,
} from '../alerts';
import * as client from '../client';
import { apiClient } from '../client';

function wrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe('alert-rules hooks', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('useAlertRules fetches from /alert-rules', async () => {
    const spy = vi.spyOn(client.apiClient, 'get')
      .mockResolvedValue({ rules: [{ id: '1', name: 'X', source: 'system' }] });
    const { result } = renderHook(() => useAlertRules(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.data?.rules).toHaveLength(1));
    expect(spy).toHaveBeenCalledWith('/alert-rules', expect.anything());
  });

  it('useCreateAlertRule posts to /alert-rules', async () => {
    const spy = vi.spyOn(client.apiClient, 'post').mockResolvedValue({ id: 'new' });
    const { result } = renderHook(() => useCreateAlertRule(), { wrapper: wrapper() });
    result.current.mutate({ name: 'N', type: 'security', severity: 'high',
                            target_filters: [{ field: 'x', op: '=', value: 'y' }] } as any);
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/alert-rules', expect.anything()));
  });

  it('useDeleteAlertRule calls DELETE', async () => {
    const spy = vi.spyOn(client.apiClient, 'del').mockResolvedValue(undefined);
    const { result } = renderHook(() => useDeleteAlertRule(), { wrapper: wrapper() });
    result.current.mutate('abc');
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/alert-rules/abc'));
  });

  it('useToggleAlertRule PATCHes enabled flag', async () => {
    const spy = vi.spyOn(client.apiClient, 'patch').mockResolvedValue({});
    const { result } = renderHook(() => useToggleAlertRule(), { wrapper: wrapper() });
    result.current.mutate({ id: 'abc', enabled: false });
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/alert-rules/abc', { enabled: false }));
  });
});

describe('anomaly-threshold hook', () => {
  it('useUpdateAnomalyThreshold PATCHes the per-rule endpoint', async () => {
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue({ id: 'r1' } as any);
    const { result } = renderHook(() => useUpdateAnomalyThreshold(), { wrapper: wrapper() });
    await act(async () => { result.current.mutate({ id: 'r1', data: { pct_medium: 0.25 } }); });
    await waitFor(() => expect(patch).toHaveBeenCalledWith('/alert-rules/r1/anomaly-threshold', { pct_medium: 0.25 }));
  });
});

describe('useAlertHistory (#905)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('fetches the per-alert firing history with a days window', async () => {
    const spy = vi.spyOn(client.apiClient, 'get')
      .mockResolvedValue({ alert_id: 'a1', history: [] });
    const { result } = renderHook(() => useAlertHistory('a1', 90), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith('/alerts/a1/history', { params: { days: 90 } });
  });
});
