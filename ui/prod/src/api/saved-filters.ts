// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';

export type SavedFilterVisibility = 'private' | 'link' | 'org';

/** The opaque Explorer view snapshot stored on a saved filter. version=1. */
export interface SavedFilterPayload {
  version: 1;
  searchQuery: string;
  columnFilters: Record<string, string[]>;
  dateFilters: Record<string, number>;
  sort: { column: string; direction: 'asc' | 'desc' };
  columns: {
    order: string[];
    visible: Record<string, boolean>;
    widths: Record<string, number>;
  };
  pivot: {
    mode: boolean;
    config: {
      rowDimension: string;
      columnDimension: string;
      columnsEnabled: boolean;
      displayMode: 'count' | 'percent_total' | 'percent_row' | 'percent_col';
    };
  };
}

export interface SavedFilter {
  id: string;
  name: string;
  description: string | null;
  payload: SavedFilterPayload;
  visibility: SavedFilterVisibility;
  access: 'owner' | 'granted' | 'org' | 'link';
  can_edit: boolean;
  created_at: string;
  updated_at: string;
}

export interface SavedFilterList {
  mine: SavedFilter[];
  shared_with_me: SavedFilter[];
  library: SavedFilter[];
}

export interface SavedFilterCreateBody {
  name: string;
  description?: string | null;
  payload: SavedFilterPayload;
  visibility?: SavedFilterVisibility;
}

export interface SavedFilterUpdateBody {
  name?: string;
  description?: string | null;
  payload?: SavedFilterPayload;
  visibility?: SavedFilterVisibility;
}

export const savedFiltersApi = {
  list: () => apiClient.get<SavedFilterList>('/saved-filters'),
  get: (id: string) => apiClient.get<SavedFilter>('/saved-filters/' + id),
  create: (body: SavedFilterCreateBody) =>
    apiClient.post<SavedFilter>('/saved-filters', body),
  update: (id: string, body: SavedFilterUpdateBody) =>
    apiClient.put<SavedFilter>('/saved-filters/' + id, body),
  remove: (id: string) => apiClient.del<{ ok: boolean }>('/saved-filters/' + id),
};

export function useSavedFilters() {
  return useApiQuery<SavedFilterList>(['saved-filters', 'list'], '/saved-filters');
}

export function useCreateSavedFilter(
  options?: Omit<UseMutationOptions<SavedFilter, Error, SavedFilterCreateBody>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<SavedFilter, Error, SavedFilterCreateBody>({
    mutationFn: (body) => savedFiltersApi.create(body),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['saved-filters'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useUpdateSavedFilter(
  options?: Omit<UseMutationOptions<SavedFilter, Error, { id: string; body: SavedFilterUpdateBody }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<SavedFilter, Error, { id: string; body: SavedFilterUpdateBody }>({
    mutationFn: ({ id, body }) => savedFiltersApi.update(id, body),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['saved-filters'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useDeleteSavedFilter(
  options?: Omit<UseMutationOptions<{ ok: boolean }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) => savedFiltersApi.remove(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['saved-filters'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}
