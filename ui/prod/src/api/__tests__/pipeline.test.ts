// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useTriggerPipeline, useDataVersion } from '../pipeline';
import { apiClient } from '../client';
import { setupMutationTest } from './helpers';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    del: vi.fn(),
  },
}));

describe('pipeline.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useTriggerPipeline invalidates pipeline AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ run_id: 'r1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useTriggerPipeline({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync();

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pipeline'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});

describe('pipeline.ts: useDataVersion, the app-wide cache-coherency poll', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('polls /pipeline/data-version once a minute under a stable key', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: 'run-1',
      completed_at: '2026-07-29T10:30:00+00:00',
      status: 'completed',
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: qc }, children);

    const { result } = renderHook(() => useDataVersion(), { wrapper });
    await waitFor(() => expect(result.current.data?.run_id).toBe('run-1'));

    // apiClient unwraps the {success, data} envelope, so the hook's data IS the
    // inner object — no `.data.data`.
    expect(result.current.data?.status).toBe('completed');
    expect(apiClient.get).toHaveBeenCalledWith('/pipeline/data-version', {
      params: undefined,
    });

    const query = qc
      .getQueryCache()
      .find({ queryKey: ['pipeline', 'data-version'] });
    expect(query).toBeDefined();
    // One request per minute per open tab is the deliberate cost of the whole
    // design; a stray edit to this number changes it for every tab at once.
    expect(query!.observers[0].options.refetchInterval).toBe(60_000);
  });
});
