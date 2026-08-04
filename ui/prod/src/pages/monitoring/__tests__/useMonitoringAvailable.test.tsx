// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useMonitoringAvailable } from '../useMonitoringAvailable';
import { apiClient } from '@/api/client';

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useMonitoringAvailable', () => {
  beforeEach(() => vi.clearAllMocks());

  it('returns true when /api/health reports features.monitoring = true', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'healthy',
      application: { features: { monitoring: true } },
    });
    const { result } = renderHook(() => useMonitoringAvailable(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current).toBe(true));
    // Hook derives from the shared useHealth() query — single /health fetch
    // shared with the sidebar version probe and the chat-availability check.
    expect(apiClient.get).toHaveBeenCalledWith('/health', { params: undefined });
  });

  it('returns false when features.monitoring = false', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'healthy',
      application: { features: { monitoring: false } },
    });
    const { result } = renderHook(() => useMonitoringAvailable(), { wrapper: makeWrapper() });
    await new Promise((r) => setTimeout(r, 30));
    expect(result.current).toBe(false);
  });

  it('returns false when application.features is missing (older API)', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'healthy',
      application: { version: '2026.28.18' },
    });
    const { result } = renderHook(() => useMonitoringAvailable(), { wrapper: makeWrapper() });
    await new Promise((r) => setTimeout(r, 30));
    expect(result.current).toBe(false);
  });

  it('returns false while health is still loading (no data yet)', () => {
    // Never resolves — mirrors the pre-fetch state. Must be false so the
    // dashboards don't mount and fire /api/monitoring/* before we know.
    (apiClient.get as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useMonitoringAvailable(), { wrapper: makeWrapper() });
    expect(result.current).toBe(false);
  });

  it('returns false when /api/health fails', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useMonitoringAvailable(), { wrapper: makeWrapper() });
    await new Promise((r) => setTimeout(r, 30));
    expect(result.current).toBe(false);
  });
});
