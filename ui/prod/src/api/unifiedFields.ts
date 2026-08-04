// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';
import type { MergeFunction } from '@/pages/settings/unified-fields/unified-fields-utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type { MergeFunction };

export interface UnifiedField {
  id: string;
  name: string;
  function: MergeFunction;
  sources: string[];
  data_type: string;
  encoded_key: string;
}

export interface PreviewRow {
  asset_id: string;
  hostname: string;
  source_values: Record<string, unknown>;
  result: unknown;
}

export interface UnifiedFieldWriteRequest {
  name: string;
  function: MergeFunction;
  sources: string[];
}

export interface UnifiedFieldPreviewRequest {
  function: MergeFunction;
  sources: string[];
  sample_size?: number;
}

export interface UnifiedFieldListResponse {
  success: boolean;
  fields: UnifiedField[];
}

export interface UnifiedFieldResponse {
  success: boolean;
  field: UnifiedField;
}

export interface PreviewResponse {
  success: boolean;
  rows: PreviewRow[];
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const unifiedFieldsApi = {
  list: () =>
    apiClient.get<UnifiedFieldListResponse>('/fields/unified'),

  get: (id: string) =>
    apiClient.get<UnifiedFieldResponse>('/fields/unified/' + id),

  create: (data: UnifiedFieldWriteRequest) =>
    apiClient.post<UnifiedFieldResponse>('/fields/unified', data),

  update: (id: string, data: UnifiedFieldWriteRequest) =>
    apiClient.put<UnifiedFieldResponse>('/fields/unified/' + id, data),

  remove: (id: string) =>
    apiClient.del<{ success: boolean; message: string }>('/fields/unified/' + id),

  preview: (data: UnifiedFieldPreviewRequest) =>
    apiClient.post<PreviewResponse>('/fields/unified/preview', data),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useUnifiedFields() {
  return useApiQuery<UnifiedFieldListResponse>(
    ['unified-fields', 'list'],
    '/fields/unified',
  );
}

// ---------------------------------------------------------------------------
// Mutation hooks
// ---------------------------------------------------------------------------

export function useCreateUnifiedField(
  options?: Omit<UseMutationOptions<UnifiedFieldResponse, Error, UnifiedFieldWriteRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: UnifiedFieldWriteRequest) => unifiedFieldsApi.create(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['unified-fields'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdateUnifiedField(
  id: string,
  options?: Omit<UseMutationOptions<UnifiedFieldResponse, Error, UnifiedFieldWriteRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: UnifiedFieldWriteRequest) => unifiedFieldsApi.update(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['unified-fields'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteUnifiedField(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (id: string) => unifiedFieldsApi.remove(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['unified-fields'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function usePreviewUnifiedField(
  options?: Omit<UseMutationOptions<PreviewResponse, Error, UnifiedFieldPreviewRequest>, 'mutationFn'>,
) {
  return useMutation({
    mutationFn: (data: UnifiedFieldPreviewRequest) => unifiedFieldsApi.preview(data),
    ...options,
  });
}
