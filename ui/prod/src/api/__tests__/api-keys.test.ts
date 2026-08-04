// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useCreateApiKey, useUpdateApiKey, useDeleteApiKey } from '../api-keys';
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

describe('api-keys.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useCreateApiKey invalidates api-keys AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'k1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreateApiKey({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'new', expires_at: '2026-12-31' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['api-keys'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateApiKey invalidates api-keys AND runs caller onSuccess', async () => {
    (apiClient.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'k1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateApiKey('k1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'renamed' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['api-keys'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeleteApiKey invalidates api-keys AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({ message: 'ok' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeleteApiKey({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('k1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['api-keys'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});
