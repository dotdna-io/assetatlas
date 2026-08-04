// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import {
  usePostureCategories,
  useCreatePostureSubRule,
  useUpdatePostureSubRule,
  useDeletePostureSubRule,
  useTogglePostureSubRule,
  usePreviewPostureSubRule,
  useExecutePostureRules,
  useUpdatePostureCategory,
  useReorderPostureSubRules,
} from '../posture-rules';
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

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // eslint-disable-next-line react/display-name
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const mockSubRulePayload = {
  name: 'Test Rule',
  target_filters: [
    {
      field_id: { kind: 'static' as const, name: 'Category' },
      operator: 'equals',
      value: 'server',
    },
  ],
  compliance_criteria: {
    logic: 'any' as const,
    conditions: [
      {
        field_id: {
          kind: 'extended_attribute' as const,
          source: 'CrowdStrike',
          attribute: 'enrolled',
        },
        operator: 'equals',
        value: 'true',
      },
    ],
  },
};

describe('usePostureCategories', () => {
  beforeEach(() => vi.clearAllMocks());

  it('GETs /posture-rules and returns categories', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      categories: [{ id: 'secured' }],
    });

    const { result } = renderHook(() => usePostureCategories(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(apiClient.get).toHaveBeenCalledWith('/posture-rules', expect.any(Object));
    expect(result.current.data?.categories).toHaveLength(1);
    expect(result.current.data?.categories[0].id).toBe('secured');
  });
});


describe('useCreatePostureSubRule', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs /posture-rules/:categoryId/sub-rules', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'sr-1' });

    const { result } = renderHook(() => useCreatePostureSubRule(), { wrapper: wrapper() });
    await result.current.mutateAsync({
      categoryId: 'secured',
      data: mockSubRulePayload,
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/posture-rules/secured/sub-rules',
      expect.objectContaining({ name: 'Test Rule' }),
    );
  });
});

describe('useUpdatePostureSubRule', () => {
  beforeEach(() => vi.clearAllMocks());

  it('PUTs /posture-rules/sub-rules/:subRuleId', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'sr-1' });

    const { result } = renderHook(() => useUpdatePostureSubRule(), { wrapper: wrapper() });
    await result.current.mutateAsync({ subRuleId: 'sr-1', data: mockSubRulePayload });

    expect(apiClient.put).toHaveBeenCalledWith(
      '/posture-rules/sub-rules/sr-1',
      expect.objectContaining({ name: 'Test Rule' }),
    );
  });
});

describe('useDeletePostureSubRule', () => {
  beforeEach(() => vi.clearAllMocks());

  it('DELETEs /posture-rules/sub-rules/:subRuleId', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);

    const { result } = renderHook(() => useDeletePostureSubRule(), { wrapper: wrapper() });
    await result.current.mutateAsync('sr-1');

    expect(apiClient.del).toHaveBeenCalledWith('/posture-rules/sub-rules/sr-1');
  });
});

describe('useTogglePostureSubRule', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs /posture-rules/sub-rules/:id/toggle with enabled payload', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'sr-1', enabled: false });

    const { result } = renderHook(() => useTogglePostureSubRule(), { wrapper: wrapper() });
    await result.current.mutateAsync({ subRuleId: 'sr-1', enabled: false });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/posture-rules/sub-rules/sr-1/toggle',
      { enabled: false },
    );
  });
});

describe('usePreviewPostureSubRule', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs /posture-rules/sub-rules/preview', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ matched: 10, pass: 8, fail: 2 });

    const { result } = renderHook(() => usePreviewPostureSubRule(), { wrapper: wrapper() });
    await result.current.mutateAsync({
      target_filters: [
        {
          field_id: { kind: 'static', name: 'Category' },
          operator: 'equals',
          value: 'server',
        },
      ],
      compliance_criteria: {
        logic: 'any',
        conditions: [
          {
            field_id: {
              kind: 'extended_attribute',
              source: 'CrowdStrike',
              attribute: 'enrolled',
            },
            operator: 'equals',
            value: 'true',
          },
        ],
      },
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/posture-rules/sub-rules/preview',
      expect.objectContaining({ target_filters: expect.any(Array) }),
    );
    await waitFor(() => expect(result.current.data?.matched).toBe(10));
  });
});

describe('useUpdatePostureCategory', () => {
  beforeEach(() => vi.clearAllMocks());

  it('PATCHes /posture-rules/categories/:id and invalidates the list even when the caller supplies onSuccess', async () => {
    // Regression for the options-spread ordering bug: when the call site
    // passes `{ onSuccess }` (PostureCategoryCard closes the inline editor
    // that way), the hook's invalidateQueries must still run — otherwise
    // the coverage card keeps the pre-save applicable_categories until the
    // user reloads.
    (apiClient.patch as ReturnType<typeof vi.fn>) = vi.fn().mockResolvedValue({
      id: 'secured',
      applicable_categories: ['workstation'],
    });

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(qc, 'invalidateQueries');
    const callerOnSuccess = vi.fn();

    const { result } = renderHook(
      () => useUpdatePostureCategory({ onSuccess: callerOnSuccess }),
      {
        wrapper: ({ children }) =>
          React.createElement(QueryClientProvider, { client: qc }, children),
      },
    );
    await result.current.mutateAsync({
      categoryId: 'secured',
      data: { applicable_categories: ['workstation'] },
    });

    expect(apiClient.patch).toHaveBeenCalledWith(
      '/posture-rules/categories/secured',
      { applicable_categories: ['workstation'] },
    );
    expect(spy).toHaveBeenCalledWith({ queryKey: ['posture-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});


describe('useExecutePostureRules', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs /posture-rules/execute with no body', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      updated_count: 42,
      duration_ms: 500,
    });

    const { result } = renderHook(() => useExecutePostureRules(), { wrapper: wrapper() });
    await result.current.mutateAsync();

    expect(apiClient.post).toHaveBeenCalledWith('/posture-rules/execute');
    await waitFor(() => expect(result.current.data?.updated_count).toBe(42));
  });
});

describe('posture-rules.ts: caller-supplied onSuccess does not clobber invalidation', () => {
  beforeEach(() => vi.clearAllMocks());

  it('useCreatePostureSubRule invalidates AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'sr-1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useCreatePostureSubRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ categoryId: 'secured', data: mockSubRulePayload });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['posture-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useUpdatePostureSubRule invalidates AND runs caller onSuccess', async () => {
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'sr-1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useUpdatePostureSubRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ subRuleId: 'sr-1', data: mockSubRulePayload });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['posture-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useDeletePostureSubRule invalidates AND runs caller onSuccess', async () => {
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useDeletePostureSubRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync('sr-1');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['posture-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useTogglePostureSubRule invalidates AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'sr-1' });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useTogglePostureSubRule({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ subRuleId: 'sr-1', enabled: false });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['posture-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useExecutePostureRules invalidates AND runs caller onSuccess', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      updated_count: 1,
      duration_ms: 10,
    });
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useExecutePostureRules({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['posture-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });

  it('useReorderPostureSubRules invalidates AND runs caller onSuccess', async () => {
    (apiClient.patch as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    const { invalidateSpy, callerOnSuccess, wrapper } = setupMutationTest();
    const { result } = renderHook(
      () => useReorderPostureSubRules({ onSuccess: callerOnSuccess }),
      { wrapper },
    );
    await result.current.mutateAsync({ categoryId: 'secured', orderedIds: ['a', 'b'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['posture-rules'] });
    expect(callerOnSuccess).toHaveBeenCalledTimes(1);
  });
});
