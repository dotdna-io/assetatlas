// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQueryClient, useMutation } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';
import { apiClient } from './client';
import { useApiQuery } from './hooks';

export type Severity = 'low' | 'medium' | 'high' | 'critical';
export const ROUTING_CATEGORIES = [
  { key: 'security', label: 'Security' },
  { key: 'compliance', label: 'Compliance' },
  { key: 'operational', label: 'Operational' },
  { key: 'data_quality', label: 'Data Quality' },
  { key: 'custom', label: 'Custom' },
] as const;

/** A masked secret comes back from the API as {configured:true} (backend
 *  AlertDeliveryService._mask). The write-only rule on update: OMIT an untouched
 *  secret from the config and the backend keeps the stored value. */
export function isSecretConfigured(value: unknown): boolean {
  return !!value && typeof value === 'object'
    && (value as { configured?: boolean }).configured === true;
}

export interface AlertChannel {
  id: string; type: string; name: string; enabled: boolean;
  config: Record<string, unknown>; created_at?: string; updated_at?: string;
}
export interface ChannelTypeInfo { type: string; label: string; }
export interface RouteRow {
  channel_id: string; channel_type: string; name: string;
  enabled: boolean; min_severity: Severity;
}
export interface TestResult { ok: boolean; response_code: number | null; error: string | null; }
export interface ChannelCreate { type: string; name: string; config: Record<string, unknown>; }
export interface ChannelUpdate { name: string; enabled: boolean; config: Record<string, unknown>; }
export interface RouteInput { channel_id: string; min_severity: Severity; }

export const channelsApi = {
  list: () => apiClient.get<AlertChannel[]>('/alerts/channels'),
  get: (id: string) => apiClient.get<AlertChannel>(`/alerts/channels/${id}`),
  create: (body: ChannelCreate) => apiClient.post<AlertChannel>('/alerts/channels', body),
  update: (id: string, body: ChannelUpdate) =>
    apiClient.put<AlertChannel>(`/alerts/channels/${id}`, body),
  remove: (id: string) => apiClient.del(`/alerts/channels/${id}`),
  test: (id: string) => apiClient.post<TestResult>(`/alerts/channels/${id}/test`),
  types: () => apiClient.get<ChannelTypeInfo[]>('/alerts/channels/types'),
  getRouting: () => apiClient.get<Record<string, RouteRow[]>>('/alerts/routing'),
  setCategoryRouting: (category: string, channels: RouteInput[]) =>
    apiClient.put(`/alerts/routing/${category}`, { channels }),
};

const KEY = ['alert-channels'];
export function useChannels() {
  return useApiQuery<AlertChannel[]>(KEY, '/alerts/channels');
}
export function useChannelTypes() {
  return useApiQuery<ChannelTypeInfo[]>(['alert-channel-types'], '/alerts/channels/types');
}
export function useRouting() {
  return useApiQuery<Record<string, RouteRow[]>>(['alert-routing'], '/alerts/routing');
}
export function useCreateChannel(
  options?: Omit<UseMutationOptions<AlertChannel, Error, ChannelCreate>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: ChannelCreate) => channelsApi.create(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ['alert-routing'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdateChannel(
  options?: Omit<UseMutationOptions<AlertChannel, Error, { id: string; body: ChannelUpdate }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ChannelUpdate }) =>
      channelsApi.update(id, body),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ['alert-routing'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteChannel(
  options?: Omit<UseMutationOptions<unknown, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (id: string) => channelsApi.remove(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ['alert-routing'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useSetCategoryRouting(
  options?: Omit<UseMutationOptions<unknown, Error, { category: string; channels: RouteInput[] }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: ({ category, channels }: { category: string; channels: RouteInput[] }) =>
      channelsApi.setCategoryRouting(category, channels),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['alert-routing'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}
