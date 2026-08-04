// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import {
  useCreateUser,
  useUpdateUser,
  useToggleUser,
  useDeleteUser,
  useResetPassword,
} from '../auth';
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

describe('auth.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useCreateUser invalidates users list AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      user: { id: 'u1' },
      message: 'ok',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreateUser({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({
      username: 'new',
      email: 'n@x.com',
      password: 'pw',
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['auth', 'users'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateUser invalidates users list AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      user: { id: 'u1' },
      message: 'ok',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateUser('u1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ email: 'new@x.com' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['auth', 'users'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useToggleUser invalidates users list AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useToggleUser({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'u1', enable: true });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['auth', 'users'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeleteUser invalidates users list AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeleteUser({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('u1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['auth', 'users'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useResetPassword invalidates users list AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useResetPassword({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'u1', data: { new_password: 'pw' } });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['auth', 'users'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});
