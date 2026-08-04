// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import {
  useUpdateSetting,
  useResetSetting,
  useUpdateFieldRegistry,
  useRegenerateField,
  useUploadEnrichmentDataset,
  useApplyEnrichmentDataset,
  useDeleteEnrichmentDataset,
  useToggleEnrichmentDataset,
  useSaveReliabilityScores,
  useResetReliabilityScores,
  useSynonymsRegenStatus,
} from '../settings';
import { settingsApi } from '../settings';
import { apiClient } from '../client';
import { setupMutationTest } from './helpers';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    del: vi.fn(),
    upload: vi.fn(),
  },
}));

describe('settings.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useUpdateSetting invalidates category key AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ value: 1 });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateSetting({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({
      category: 'asset_generation',
      key: 'foo',
      data: { value: 1 } as any,
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['settings', 'category', 'asset_generation'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useResetSetting invalidates category key AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ value: null });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useResetSetting({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ category: 'asset_generation', key: 'foo' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['settings', 'category', 'asset_generation'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdateFieldRegistry invalidates field-registry AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdateFieldRegistry({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ fieldName: 'hostname', data: { description: 'x' } as any });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['settings', 'field-registry'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useRegenerateField invalidates field-registry AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useRegenerateField({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('hostname');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['settings', 'field-registry'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUploadEnrichmentDataset invalidates enrichment AND runs caller onSuccess', async () => {
    (apiClient.upload as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'd1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUploadEnrichmentDataset({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync(new FormData());
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['enrichment'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useApplyEnrichmentDataset invalidates enrichment AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useApplyEnrichmentDataset({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('d1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['enrichment'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeleteEnrichmentDataset invalidates enrichment AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({ message: 'ok' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeleteEnrichmentDataset({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('d1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['enrichment'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useToggleEnrichmentDataset PUTs the /status sub-resource, invalidates enrichment AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useToggleEnrichmentDataset({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ id: 'd1', status: 'disabled' });
    // Regression: the hook used to PUT the bare /datasets/<id> path, which the
    // backend exposes only for GET/DELETE — so toggling returned 405. The
    // status endpoint lives at /datasets/<id>/status.
    expect(apiClient.put).toHaveBeenCalledWith('/enrichment/datasets/d1/status', { status: 'disabled' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['enrichment'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useSaveReliabilityScores invalidates settings AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useSaveReliabilityScores({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ crowdstrike: 10 });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['settings'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useResetReliabilityScores invalidates settings AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useResetReliabilityScores({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['settings'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

});

describe('settingsApi.email', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('getEmail calls GET /settings/email', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      enabled: true,
      smtp_host: 'smtp.example.com',
      smtp_port: 587,
      smtp_security: 'starttls',
      smtp_username: 'u',
      smtp_password: '__SET__',
      from_address: 'no-reply@example.com',
      from_display_name: 'AssetAtlas',
      reply_to_address: '',
    });
    const res = await settingsApi.getEmail();
    expect(apiClient.get).toHaveBeenCalledWith('/settings/email');
    expect(res.smtp_password).toBe('__SET__');
  });

  it('updateEmail calls PUT /settings/email/<key> with {value}', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({});
    await settingsApi.updateEmail('smtp_port', 465);
    expect(apiClient.put).toHaveBeenCalledWith('/settings/email/smtp_port', { value: 465 });
  });

  it('sendTestEmail calls POST /settings/email/test with recipient', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const res = await settingsApi.sendTestEmail('admin@example.com');
    expect(apiClient.post).toHaveBeenCalledWith('/settings/email/test', { recipient: 'admin@example.com' });
    expect(res.success).toBe(true);
  });
});

describe('settings.ts: LLM admin actions', () => {
  beforeEach(() => vi.clearAllMocks());

  it('regenerateField posts to /regenerate with a long timeout, not the 30s default', () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    settingsApi.regenerateField('hostname');
    expect(apiClient.post).toHaveBeenCalledWith(
      '/settings/field-registry/hostname/regenerate',
      undefined,
      { timeout: 90_000 },
    );
  });

  it('regenerateSynonyms posts to the async start endpoint (fire-and-poll)', () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'started' });
    settingsApi.regenerateSynonyms();
    expect(apiClient.post).toHaveBeenCalledWith('/settings/synonyms/regenerate');
  });

  it('getSynonymsRegenStatus reads the status endpoint', () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ running: false, last_result: null });
    settingsApi.getSynonymsRegenStatus();
    expect(apiClient.get).toHaveBeenCalledWith('/settings/synonyms/status');
  });
});

describe('useSynonymsRegenStatus: per-cycle query key', () => {
  beforeEach(() => vi.clearAllMocks());

  it('keys the status query on runId so each cycle gets a fresh cache entry', () => {
    // Never-resolving so the query stays pending (no late state update / act warning);
    // the query key is registered synchronously on render regardless.
    (apiClient.get as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const qc = new QueryClient();
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);

    const { rerender } = renderHook(
      ({ runId }: { runId: number }) => useSynonymsRegenStatus(true, runId),
      { wrapper, initialProps: { runId: 1 } },
    );
    rerender({ runId: 2 });

    const keys = qc.getQueryCache().getAll().map((q) => q.queryKey);
    // Distinct entries per cycle. Pre-fix (a constant key) these collapse into one,
    // so re-enabling within staleTime served the prior cycle's stale running:false
    // and fired a false "done".
    expect(keys).toContainEqual(['settings', 'synonyms-regen-status', 1]);
    expect(keys).toContainEqual(['settings', 'synonyms-regen-status', 2]);
  });
});
