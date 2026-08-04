// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Report {
  id: string;
  title: string;
  description: string | null;
  charts: Record<string, any>[];
  is_default: boolean;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
  last_modified: string;
}

export interface ReportListParams {
  page?: number;
  limit?: number;
  search?: string;
}

export interface ReportListResponse {
  reports: Report[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export interface ReportCreateRequest {
  title: string;
  description?: string;
  charts: Record<string, any>[];
}

export interface ReportUpdateRequest {
  title?: string;
  description?: string;
  charts?: Record<string, any>[];
  expected_last_modified?: string;
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const reportsApi = {
  list: (params?: ReportListParams) =>
    apiClient.get<ReportListResponse>('/reports', { params: params as Record<string, any> }),

  get: (id: string) =>
    apiClient.get<Report>('/reports/' + id),

  getDefault: () =>
    apiClient.get<Report>('/reports/default'),

  create: (data: ReportCreateRequest) =>
    apiClient.post<Report>('/reports', data),

  update: (id: string, data: ReportUpdateRequest) =>
    apiClient.put<Report>('/reports/' + id, data),

  delete: (id: string) =>
    apiClient.del<{ success: boolean; message: string }>('/reports/' + id),

  clone: (id: string) =>
    apiClient.post<Report>('/reports/' + id + '/clone'),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useReports(params?: ReportListParams) {
  return useApiQuery<ReportListResponse>(
    ['reports', 'list', JSON.stringify(params)],
    '/reports',
    { params: params as Record<string, any> },
  );
}

export function useReport(id: string | undefined) {
  return useApiQuery<Report>(
    ['reports', 'detail', id],
    '/reports/' + id,
    { enabled: !!id },
  );
}

export function useDefaultReport() {
  return useApiQuery<Report>(
    ['reports', 'default'],
    '/reports/default',
  );
}

export function useCreateReport(
  options?: Omit<UseMutationOptions<Report, Error, ReportCreateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Report, Error, ReportCreateRequest>({
    mutationFn: (data) => reportsApi.create(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['reports'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdateReport(
  id: string,
  options?: Omit<UseMutationOptions<Report, Error, ReportUpdateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Report, Error, ReportUpdateRequest>({
    mutationFn: (data) => reportsApi.update(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['reports'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteReport(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ success: boolean; message: string }, Error, string>({
    mutationFn: (id) => reportsApi.delete(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['reports'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useCloneReport(
  options?: Omit<UseMutationOptions<Report, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Report, Error, string>({
    mutationFn: (id) => reportsApi.clone(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['reports'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}
