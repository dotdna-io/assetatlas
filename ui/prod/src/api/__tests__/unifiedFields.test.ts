// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { unifiedFieldsApi, useCreateUnifiedField, useUpdateUnifiedField, useDeleteUnifiedField } from '../unifiedFields';
import { apiClient } from '../client';
import { setupMutationTest } from './helpers';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    del: vi.fn(),
  },
}));

describe('unifiedFieldsApi.preview', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs to /fields/unified/preview with the draft body and returns rows', async () => {
    const mockRows = [
      { asset_id: 'a1', hostname: 'host1', source_values: { crowdstrike: 'host1' }, result: 'host1' },
    ];
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true, rows: mockRows });

    const draft = { function: 'coalesce' as const, sources: ['crowdstrike'] };
    const res = await unifiedFieldsApi.preview(draft);

    expect(apiClient.post).toHaveBeenCalledWith('/fields/unified/preview', draft);
    expect(res.rows).toBe(mockRows);
  });
});

describe('unifiedFields.ts: mutation hooks invalidate unified-fields and run caller onSuccess', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useCreateUnifiedField invalidates unified-fields AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      field: { id: 'f1', name: 'hostname', function: 'coalesce', sources: [], data_type: 'string', encoded_key: 'hostname' },
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreateUnifiedField({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'hostname', function: 'coalesce', sources: [] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['unified-fields'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateUnifiedField invalidates unified-fields AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      field: { id: 'f1', name: 'hostname', function: 'coalesce', sources: [], data_type: 'string', encoded_key: 'hostname' },
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateUnifiedField('f1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'hostname_updated', function: 'coalesce', sources: [] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['unified-fields'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeleteUnifiedField invalidates unified-fields AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true, message: 'deleted' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeleteUnifiedField({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('f1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['unified-fields'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});
