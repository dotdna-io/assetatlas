// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import {
  useClassifyAsset,
  useAddTag,
  useRemoveTag,
  useMergeAssets,
  useUnmergeAsset,
  useAssets,
  useClassifyBulk,
  useClearClassifyBulk,
  useTagBulk,
  useUntagBulk,
  useTagBulkCandidates,
  assetsApi,
} from '../assets';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { waitFor } from '@testing-library/react';
import React from 'react';
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

describe('assets.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useClassifyAsset invalidates assets AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useClassifyAsset({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'a1', data: { category: 'server' } as any });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useAddTag invalidates assets AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useAddTag({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ assetId: 'a1', data: { name: 'prod' } as any });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useRemoveTag invalidates assets AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({ message: 'ok' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useRemoveTag({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ assetId: 'a1', tagId: 't1' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useMergeAssets invalidates assets AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useMergeAssets({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ source_id: 'a1', target_id: 'a2' } as any);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUnmergeAsset invalidates assets AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUnmergeAsset({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'a1', data: { source_id: 'src' } as any });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// useAssets: gating behaviour. Bare GET /api/assets fails @validate_query on
// the backend (decorator instantiates the Pydantic model from empty
// request.args and rejects). Callers express "not ready yet" by passing
// `undefined`; the hook must NOT fire in that case.
// ---------------------------------------------------------------------------
describe('useAssets: enabled gate', () => {
  beforeEach(() => vi.clearAllMocks());

  function makeWrapper() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: qc }, children);
    return { qc, wrapper };
  }

  it('does NOT fetch when params is undefined', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ assets: [], pagination: {} });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useAssets(undefined), { wrapper });
    // Yield a few microtasks; React Query won't fire a disabled query.
    await new Promise((r) => setTimeout(r, 30));
    expect(apiClient.get).not.toHaveBeenCalled();
    expect(result.current.isFetching).toBe(false);
  });

  it('DOES fetch when params is provided (even if empty)', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ assets: [], pagination: {} });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useAssets({}), { wrapper });
    await waitFor(() => expect(apiClient.get).toHaveBeenCalled());
    expect((apiClient.get as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe('/assets');
    expect(result.current.isError).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// assetsApi bulk: raw API method request shapes
// ---------------------------------------------------------------------------
describe('assetsApi bulk', () => {
  beforeEach(() => vi.clearAllMocks());

  it('classifyBulk POSTs filters + category to /assets/classify-bulk', async () => {
    // The alert scope rides in filters[] now (#1011's target-set resolution
    // still applies — the server resolves against the same filters the count
    // used — but alert_id itself was dropped from the bulk payload once the
    // alert became an ordinary filters[] entry).
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ updated: 3 });
    const res = await assetsApi.classifyBulk({
      filters: [{ field_id: 'x', op: 'eq', value: 'y' }],
      search: 'printer',
      category: 'Network',
      subcategory: 'Switch',
    });
    expect(apiClient.post).toHaveBeenCalledWith('/assets/classify-bulk', {
      filters: [{ field_id: 'x', op: 'eq', value: 'y' }],
      search: 'printer',
      category: 'Network',
      subcategory: 'Switch',
    });
    expect(res).toEqual({ updated: 3 });
  });

  it('clearClassifyBulk POSTs filters + search to /assets/classify-bulk/clear', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ cleared: 2 });
    const res = await assetsApi.clearClassifyBulk({
      filters: [],
      search: 'x',
    });
    expect(apiClient.post).toHaveBeenCalledWith('/assets/classify-bulk/clear', {
      filters: [],
      search: 'x',
    });
    expect(res).toEqual({ cleared: 2 });
  });

  it('tagBulkCandidates GETs JSON-encoded filters to /assets/tags-bulk/candidates', async () => {
    // A GET can't carry the action's JSON body, so filters travel encoded in
    // the query string — the shape the backend decodes before handing the dict
    // to the same _resolve_bulk_ids the POST routes use.
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      candidates: [{ tag_name: 'prod', count: 4 }],
    });
    const res = await assetsApi.tagBulkCandidates({
      filters: [{ field_id: 'x', op: 'eq', value: 'y' }],
      search: 'printer',
    });
    expect(apiClient.get).toHaveBeenCalledWith('/assets/tags-bulk/candidates', {
      params: {
        filters: JSON.stringify([{ field_id: 'x', op: 'eq', value: 'y' }]),
        search: 'printer',
      },
    });
    expect(res).toEqual({ candidates: [{ tag_name: 'prod', count: 4 }] });
  });

  it('tagBulkCandidates sends an empty filter array rather than omitting it', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ candidates: [] });
    await assetsApi.tagBulkCandidates({});
    expect(apiClient.get).toHaveBeenCalledWith('/assets/tags-bulk/candidates', {
      params: { filters: '[]' },
    });
  });

  it('untagBulk POSTs filters + tag_name to /assets/tags-bulk/remove', async () => {
    // No tag_color: removal keys on the name alone.
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ removed: 3 });
    const res = await assetsApi.untagBulk({
      filters: [{ field_id: 'x', op: 'eq', value: 'y' }],
      search: 'printer',
      tag_name: 'needs-patching',
    });
    expect(apiClient.post).toHaveBeenCalledWith('/assets/tags-bulk/remove', {
      filters: [{ field_id: 'x', op: 'eq', value: 'y' }],
      search: 'printer',
      tag_name: 'needs-patching',
    });
    expect(res).toEqual({ removed: 3 });
  });

  it('tagBulk POSTs filters + tag to /assets/tags-bulk', async () => {
    // Same filter-scoped shape as its two siblings — the alert scope rides in
    // filters[], so there is no alert_id to drop here either.
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ tagged: 3, created: 2 });
    const res = await assetsApi.tagBulk({
      filters: [{ field_id: 'x', op: 'eq', value: 'y' }],
      search: 'printer',
      tag_name: 'needs-patching',
      tag_color: '#ef4444',
    });
    expect(apiClient.post).toHaveBeenCalledWith('/assets/tags-bulk', {
      filters: [{ field_id: 'x', op: 'eq', value: 'y' }],
      search: 'printer',
      tag_name: 'needs-patching',
      tag_color: '#ef4444',
    });
    expect(res).toEqual({ tagged: 3, created: 2 });
  });
});

// ---------------------------------------------------------------------------
// useClassifyBulk / useClearClassifyBulk: invalidation + caller onSuccess
// ---------------------------------------------------------------------------
describe('bulk hooks: invalidation + caller onSuccess', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useClassifyBulk invalidates assets AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ updated: 5 });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useClassifyBulk({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({
      filters: [],
      search: '',
      category: 'Network',
      subcategory: 'Switch',
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useClearClassifyBulk invalidates assets AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ cleared: 2 });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useClearClassifyBulk({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ filters: [], search: '' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useTagBulk invalidates assets AND runs caller onSuccess', async () => {
    // Invalidation matters more here than for the classify pair: the tag only
    // becomes visible once the refetched rows carry the refreshed tags column.
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ tagged: 3, created: 3 });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useTagBulk({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({
      filters: [],
      search: '',
      tag_name: 'prod',
      tag_color: '#ef4444',
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUntagBulk invalidates assets AND runs caller onSuccess', async () => {
    // Invalidation is what makes the removal visible: the rows only lose the
    // tag once the refetch brings back the refreshed assets.tags map. The key
    // also covers the candidates query, so reopening the dialog re-counts.
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ removed: 3 });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUntagBulk({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ filters: [], search: '', tag_name: 'prod' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['assets'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// useTagBulkCandidates: the picker load
// ---------------------------------------------------------------------------
describe('useTagBulkCandidates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // App.tsx's client defaults to retry: 1 — mirrored here so the hook's own
  // retry override is what the retry test actually exercises.
  function makeWrapper() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: qc }, children);
    return { wrapper };
  }

  it('encodes the live filters into the query string', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ candidates: [] });
    const { wrapper } = makeWrapper();
    renderHook(
      () => useTagBulkCandidates({ filters: [{ field_id: 'x', op: 'eq', value: 'y' }] }),
      { wrapper },
    );
    await waitFor(() => expect(apiClient.get).toHaveBeenCalled());
    expect((apiClient.get as ReturnType<typeof vi.fn>).mock.calls[0]).toEqual([
      '/assets/tags-bulk/candidates',
      { params: { filters: JSON.stringify([{ field_id: 'x', op: 'eq', value: 'y' }]) } },
    ]);
  });

  it('does not retry a rejected load', async () => {
    // The dominant failure is the cap 400 — deterministic, so a retry only
    // delays the message the dialog has to show.
    (apiClient.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('over cap'));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useTagBulkCandidates({ filters: [] }), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });
});
