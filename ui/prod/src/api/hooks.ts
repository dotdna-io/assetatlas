// SPDX-License-Identifier: AGPL-3.0-or-later
import {
  useQuery,
  useMutation,
  type UseQueryOptions,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { apiClient } from './client';

export function useApiQuery<T>(
  key: unknown[],
  url: string,
  options?: Omit<UseQueryOptions<T>, 'queryKey' | 'queryFn'> & {
    params?: Record<string, any>;
  }
) {
  const { params, ...queryOptions } = options ?? {};

  return useQuery<T>({
    queryKey: key,
    queryFn: () => apiClient.get<T>(url, { params }),
    ...queryOptions,
  });
}

export function useApiPost<TData, TBody = any>(
  url: string,
  options?: Omit<UseMutationOptions<TData, Error, TBody>, 'mutationFn'>
) {
  return useMutation<TData, Error, TBody>({
    mutationFn: (body: TBody) => apiClient.post<TData>(url, body),
    ...options,
  });
}

export function useApiPut<TData, TBody = any>(
  url: string,
  options?: Omit<UseMutationOptions<TData, Error, TBody>, 'mutationFn'>
) {
  return useMutation<TData, Error, TBody>({
    mutationFn: (body: TBody) => apiClient.put<TData>(url, body),
    ...options,
  });
}

export function useApiDelete<TData>(
  url: string,
  options?: Omit<UseMutationOptions<TData, Error, void>, 'mutationFn'>
) {
  return useMutation<TData, Error, void>({
    mutationFn: () => apiClient.del<TData>(url),
    ...options,
  });
}
