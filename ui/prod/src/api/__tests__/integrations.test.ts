// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import {
  useCreateIntegration,
  useUpdateIntegration,
  useDeleteIntegration,
  useRunIntegration,
  useToggleIntegration,
  useUpdateSchedule,
  useCreateGroup,
  useUpdateGroup,
  useDeleteGroup,
  useAddGroupMembers,
  useRemoveGroupMember,
  integrationsApi,
} from '../integrations';
import type { IntegrationGroup } from '../integrations';
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

// useDeleteGroup excluded: its wrapper onSuccess is a forwarder and
// invalidation runs in onSettled, so the bug doesn't manifest there.
describe('integrations.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useCreateIntegration invalidates integrations AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
      integration: { id: 'i1' },
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreateIntegration({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'x' } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateIntegration invalidates integrations AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
      integration: { id: 'i1' },
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateIntegration('i1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'renamed' } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeleteIntegration invalidates integrations AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeleteIntegration({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('i1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useRunIntegration invalidates integrations AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
      integration_id: 'i1',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useRunIntegration({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('i1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useToggleIntegration invalidates integrations AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useToggleIntegration({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'i1', enable: true });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateSchedule invalidates integrations AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      message: 'ok',
      schedule: { cron: '* * * * *' },
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateSchedule('i1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ cron: '* * * * *' } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useCreateGroup invalidates integrations AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      group: { id: 'g1' },
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreateGroup({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'g' } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateGroup invalidates integrations without surgically patching AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      group: { id: 'g1', name: 'renamed' },
    });
    const { qc, invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const setQueriesDataSpy = vi.spyOn(qc, 'setQueriesData');
    const { result } = renderHook(
      () => useUpdateGroup('g1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ name: 'renamed' } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    // Invalidation is the single source of truth for the cache update. The previous
    // setQueriesData patch was immediately overwritten by the refetch, so it was
    // dead-equivalent work.
    expect(setQueriesDataSpy).not.toHaveBeenCalled();
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useAddGroupMembers invalidates integrations exactly once AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useAddGroupMembers('g1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync(['i1']);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    // ['integrations', 'list'] is a strict subset of ['integrations'] under React Query prefix
    // matching, so the hook should invalidate exactly once — not twice.
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useRemoveGroupMember invalidates integrations exactly once AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useRemoveGroupMember('g1', { onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('i1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations'], refetchType: 'all' });
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});

describe('integrations.ts: useDeleteGroup preserves optimistic rollback when caller passes onError', () => {
  beforeEach(() => vi.clearAllMocks());

  it('restores the groups cache on failure even when caller supplies onError', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'));

    const { qc, wrapper } = setupMutationTest();
    const initialGroups: { success: boolean; groups: IntegrationGroup[] } = {
      success: true,
      groups: [
        { id: 'g1', name: 'G1', integration_type: 't', logo: null, member_count: 0 },
      ],
    };
    qc.setQueryData(['integrations', 'groups'], initialGroups);

    const callerOnError = vi.fn();
    const { result } = renderHook(
      () => useDeleteGroup({ onError: callerOnError }),
      { wrapper },
    );

    await result.current.mutateAsync('g1').catch(() => {});

    // Wrapper's onError must still run → cache rolled back to pre-mutation state.
    expect(qc.getQueryData(['integrations', 'groups'])).toEqual(initialGroups);
    // Caller's onError also runs.
    expect(callerOnError).toHaveBeenCalledTimes(1);
  });
});

describe('testCustomHttp', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs to /integrations/connectors/custom_http/test with the config', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      status_code: 200,
    });

    await integrationsApi.testCustomHttp({
      method: 'GET',
      url: 'https://x',
      static_query_params: {}, static_body: null, headers: {},
      auth_type: 'none',
      api_key_location: 'header', api_key_name: 'X-API-Key', api_key_prefix: '',
      pagination_type: 'none',
      page_size: 100, offset_param: 'offset', limit_param: 'limit',
      total_path: '',
      cursor_request_param: 'cursor', cursor_response_path: '$.next',
      records_root: '$.items', field_bindings: [],
      max_pages: 500, request_timeout_seconds: 30,
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/integrations/connectors/custom_http/test',
      expect.objectContaining({ url: 'https://x', auth_type: 'none' }),
    );
    // No integration_id in create mode.
    const [, body] = (apiClient.post as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(body).not.toHaveProperty('integration_id');
  });

  it('includes integration_id when editing so the backend can use the saved secret', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      status_code: 200,
    });

    await integrationsApi.testCustomHttp(
      {
        method: 'GET',
        url: 'https://x',
        static_query_params: {}, static_body: null, headers: {},
        auth_type: 'basic',
        api_key_location: 'header', api_key_name: 'X-API-Key', api_key_prefix: '',
        pagination_type: 'none',
        page_size: 100, offset_param: 'offset', limit_param: 'limit',
        total_path: '',
        cursor_request_param: 'cursor', cursor_response_path: '$.next',
        records_root: '$.items', field_bindings: [],
        max_pages: 500, request_timeout_seconds: 30,
      },
      '2c9fbf9d-b6ac-4ada-8d21-5d0707d89017',
    );

    expect(apiClient.post).toHaveBeenCalledWith(
      '/integrations/connectors/custom_http/test',
      expect.objectContaining({
        url: 'https://x',
        integration_id: '2c9fbf9d-b6ac-4ada-8d21-5d0707d89017',
      }),
    );
  });
});
