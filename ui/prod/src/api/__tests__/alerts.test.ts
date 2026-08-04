// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { usePatchAlert } from '../alerts';
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

describe('alerts.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('usePatchAlert invalidates alerts and dashboard AND runs caller onSuccess', async () => {
    (apiClient.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'a1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => usePatchAlert({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'a1', data: { status: 'resolved' } as any });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alerts'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['dashboard', 'alerts'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});
