// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQueryClient, type UseMutationOptions } from '@tanstack/react-query';
import { apiClient } from './client';
import { useApiQuery } from './hooks';

export interface SplitMergeKey {
  type: string;   // 'hostname' | 'mac' | 'serial'
  value: string;
  component: string; // canonical form to send back as `bridge` (e.g. 'sn:abc123' for serials)
}

export interface SplitPreview {
  merge_keys: SplitMergeKey[];
  bridge: string | null;
  clusters: string[][];
  would_split: boolean;
  warnings: string[];
  next_run_at: string | null;
}

export interface SplitBody {
  bridge: string[];
  primary_cluster: string[];
  clusters: string[][];
}

export interface SplitResult {
  primary_id: string;
  split_into: string[];
  next_run_at: string | null;
}

export function useSplitPreview(assetId: string | null, bridge?: string) {
  return useApiQuery<SplitPreview>(
    ['asset-split-preview', assetId, bridge ?? null],
    `/assets/${assetId}/split-preview`,
    { enabled: !!assetId, staleTime: 0, params: bridge ? { bridge } : undefined },
  );
}

export function useExecuteSplit(
  assetId: string,
  options?: Omit<UseMutationOptions<SplitResult, Error, SplitBody>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<SplitResult, Error, SplitBody>({
    mutationFn: (body) => apiClient.post<SplitResult>(`/assets/${assetId}/split`, body),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['asset-split-preview', assetId] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}
