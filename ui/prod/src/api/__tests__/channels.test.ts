// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import {
  channelsApi,
  isSecretConfigured,
  useCreateChannel,
  useUpdateChannel,
  useDeleteChannel,
  useSetCategoryRouting,
} from '../channels';
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

describe('channelsApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('list() GETs /alerts/channels', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    await channelsApi.list();
    expect(apiClient.get).toHaveBeenCalledWith('/alerts/channels');
  });

  it('get(id) GETs /alerts/channels/<id>', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'c1' });
    await channelsApi.get('c1');
    expect(apiClient.get).toHaveBeenCalledWith('/alerts/channels/c1');
  });

  it('create() POSTs /alerts/channels with the body', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'c1' });
    const body = { type: 'webhook', name: 'My Webhook', config: { url: 'https://x' } };
    await channelsApi.create(body);
    expect(apiClient.post).toHaveBeenCalledWith('/alerts/channels', body);
  });

  it('update(id, body) PUTs /alerts/channels/<id> with the body', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'c1' });
    const body = { name: 'Renamed', enabled: false, config: {} };
    await channelsApi.update('c1', body);
    expect(apiClient.put).toHaveBeenCalledWith('/alerts/channels/c1', body);
  });

  it('remove(id) DELETEs /alerts/channels/<id>', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    await channelsApi.remove('c1');
    expect(apiClient.del).toHaveBeenCalledWith('/alerts/channels/c1');
  });

  it('test(id) POSTs /alerts/channels/<id>/test', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, response_code: 200, error: null });
    await channelsApi.test('c1');
    expect(apiClient.post).toHaveBeenCalledWith('/alerts/channels/c1/test');
  });

  it('types() GETs /alerts/channels/types', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    await channelsApi.types();
    expect(apiClient.get).toHaveBeenCalledWith('/alerts/channels/types');
  });

  it('getRouting() GETs /alerts/routing', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({});
    await channelsApi.getRouting();
    expect(apiClient.get).toHaveBeenCalledWith('/alerts/routing');
  });

  it("setCategoryRouting('security', [...]) PUTs /alerts/routing/security with {channels:[...]}", async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const channels = [{ channel_id: 'c1', min_severity: 'high' as const }];
    await channelsApi.setCategoryRouting('security', channels);
    expect(apiClient.put).toHaveBeenCalledWith('/alerts/routing/security', { channels });
  });
});

// Regression for #918 review finding: the mutation hooks must forward a
// caller-supplied onSuccess alongside their own cache invalidation, not
// clobber it with a hardcoded handler (mirrors integrations.test.ts).
describe('channels.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useCreateChannel invalidates channels + routing AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'c1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreateChannel({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ type: 'webhook', name: 'x', config: {} });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alert-channels'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alert-routing'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateChannel invalidates channels + routing AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'c1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateChannel({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'c1', body: { name: 'Renamed', enabled: true, config: {} } });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alert-channels'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alert-routing'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeleteChannel invalidates channels + routing AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeleteChannel({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('c1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alert-channels'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alert-routing'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useSetCategoryRouting invalidates routing AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useSetCategoryRouting({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ category: 'security', channels: [{ channel_id: 'c1', min_severity: 'high' }] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['alert-routing'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});

describe('isSecretConfigured', () => {
  it('returns true for the {configured:true} sentinel', () => {
    expect(isSecretConfigured({ configured: true })).toBe(true);
  });

  it('returns false for a real string value', () => {
    expect(isSecretConfigured('https://hooks.example.com/abc')).toBe(false);
  });

  it('returns false for undefined/null/empty', () => {
    expect(isSecretConfigured(undefined)).toBe(false);
    expect(isSecretConfigured(null)).toBe(false);
    expect(isSecretConfigured('')).toBe(false);
  });

  it('returns false for an object without configured:true', () => {
    expect(isSecretConfigured({ configured: false })).toBe(false);
    expect(isSecretConfigured({})).toBe(false);
  });
});
