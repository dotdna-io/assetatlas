// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import {
  useCreateTagRule,
  useUpdateTagRule,
  useDeleteTagRule,
  useExecuteTagRule,
  useToggleTagRule,
  useReorderTagRules,
} from '../tag-rules';
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

describe('tag-rules.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useCreateTagRule invalidates tag-rules AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 't1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreateTagRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'r' } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['tag-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateTagRule invalidates tag-rules AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 't1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateTagRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 't1', data: { name: 'renamed' } as any });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['tag-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeleteTagRule invalidates tag-rules AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeleteTagRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('t1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['tag-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useExecuteTagRule invalidates tag-rules AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useExecuteTagRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('t1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['tag-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useToggleTagRule invalidates tag-rules AND runs caller onSuccess', async () => {
    (apiClient.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 't1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useToggleTagRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('t1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['tag-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useReorderTagRules invalidates tag-rules AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useReorderTagRules({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ orderedIds: ['a', 'b'] } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['tag-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});
