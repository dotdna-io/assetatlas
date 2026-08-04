// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import { useApiQuery } from './hooks';

export interface DatasetStatus {
  name: string;
  rows_after: number;
  rows_before: number;
  applied: boolean;
  skipped_reason?: string | null;
}

export interface DatasetSummary {
  name: string;
  row_count: number;
  source: string;
}

export interface ReferenceDataHistoryEntry {
  id: string;
  bundle_version: string;
  source: 'github' | 'upload';
  initiated_by: string | null;
  started_at: string;
  finished_at: string | null;
  status: 'running' | 'success' | 'failed';
  error_message: string | null;
  datasets: DatasetStatus[];
}

export interface ReferenceDataStatus {
  current_bundle_version: string | null;
  last_applied_at: string | null;
  datasets: DatasetStatus[];
  history: ReferenceDataHistoryEntry[];
}

export interface CheckResult {
  available: boolean;
  upstream_version: string;
  current_version: string | null;
  generated_at?: string;
  app_compat_min?: string;
  datasets?: DatasetSummary[];
  reason?: 'invalid';
  message?: string;
  status?: 'upstream_unavailable';
}

export interface ApplyResult {
  bundle_version: string;
  source: 'github' | 'upload';
  status: 'success' | 'failed';
  datasets: DatasetStatus[];
  error_message?: string | null;
  message?: string;
}

export function useReferenceDataStatus() {
  return useApiQuery<ReferenceDataStatus>(
    ['reference-data', 'status'],
    '/reference-data/status',
  );
}

export function useCheckForUpdates() {
  return useMutation<CheckResult, Error, void>({
    mutationFn: () => apiClient.post<CheckResult>('/reference-data/check'),
  });
}

export interface ApplyFromUpstreamArgs {
  version?: string;
}

export function useApplyFromUpstream() {
  const qc = useQueryClient();
  return useMutation<ApplyResult, Error, ApplyFromUpstreamArgs>({
    mutationFn: (args) =>
      apiClient.post<ApplyResult>('/reference-data/apply-from-upstream', args ?? {}),
    onSettled: () => qc.invalidateQueries({ queryKey: ['reference-data', 'status'] }),
  });
}

export function useUploadBundle() {
  const qc = useQueryClient();
  return useMutation<ApplyResult, Error, File>({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append('bundle', file);
      return apiClient.upload<ApplyResult>('/reference-data/upload', fd);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['reference-data', 'status'] }),
  });
}
