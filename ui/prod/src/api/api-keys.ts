// SPDX-License-Identifier: AGPL-3.0-or-later
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import type { UseMutationOptions } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ApiKey {
  id: string;
  user_id: string;
  name: string;
  key_prefix: string;
  expires_at: string;
  is_active: boolean;
  last_used_at: string | null;
  last_used_ip: string | null;
  created_at: string;
  created_by: string | null;
}

export interface ApiKeyListResponse {
  api_keys: ApiKey[];
  total: number;
}

export interface CreateApiKeyRequest {
  name: string;
  expires_at: string;
}

export interface CreateApiKeyResponse {
  id: string;
  name: string;
  key: string;
  key_prefix: string;
  expires_at: string;
  created_at: string;
}

export interface UpdateApiKeyRequest {
  name?: string;
  is_active?: boolean;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useApiKeys() {
  return useApiQuery<ApiKeyListResponse>(
    ['api-keys'],
    '/api-keys',
  );
}

export function useCreateApiKey(
  options?: Omit<UseMutationOptions<CreateApiKeyResponse, Error, CreateApiKeyRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<CreateApiKeyResponse, Error, CreateApiKeyRequest>({
    mutationFn: (data) => apiClient.post<CreateApiKeyResponse>('/api-keys', data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdateApiKey(
  id: string,
  options?: Omit<UseMutationOptions<ApiKey, Error, UpdateApiKeyRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<ApiKey, Error, UpdateApiKeyRequest>({
    mutationFn: (data) => apiClient.patch<ApiKey>(`/api-keys/${id}`, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteApiKey(
  options?: Omit<UseMutationOptions<{ message: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ message: string }, Error, string>({
    mutationFn: (id) => apiClient.del<{ message: string }>(`/api-keys/${id}`),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}
