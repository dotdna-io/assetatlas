// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';
import type { FieldId, FieldCatalogEntry } from '@/types/field-id';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Asset {
  id: string;
  hostname: string | null;
  primary_ip: string | null;
  mac_address: string | null;
  serial_number: string | null;
  os_type: string | null;
  os_version: string | null;
  asset_type: string | null;
  category: string | null;
  subcategory: string | null;
  data_source: string[];
  confidence_score: number | null;
  confidence_level: string | null;
  tags: Record<string, any>[];
  providers: Record<string, any>[];
  first_seen: string | null;
  last_seen: string | null;
  extended_attributes: Record<string, any> | null;
  [key: string]: any;
}

export interface AssetListParams {
  page?: number;
  limit?: number;
  fields?: string;
  sort_by?: string;
  sort_dir?: 'asc' | 'desc';
  search?: string;
  asset_type?: string;
  filters?: string; // JSON-encoded AssetFilter[]
}

export interface AssetFilter {
  field_id: FieldId;
  op: string;
  value?: unknown;
}

export interface AssetListResponse {
  assets: Record<string, unknown>[];
  pagination: {
    total: number;
    pages: number;
    page: number;
    limit: number;
  };
}

export interface AssetSearchParams {
  q: string;
  field?: 'hostname' | 'ip' | 'mac' | 'serial';
  limit?: number;
}

export interface AssetSearchResponse {
  results: {
    id: string;
    hostname: string | null;
    primary_ip: string | null;
    mac_address: string | null;
    serial_number: string | null;
    match_score: number;
  }[];
  count: number;
  query: string;
}

/**
 * After the API client unwraps `{ data, success }`, we receive the inner payload.
 *
 * Backend `GET /api/assets/fields` returns a flat list of catalog entries;
 * each entry carries a `category` discriminator ("target" | "advanced" |
 * "extended_attributes" | "tags" | "enrichment" | "posture" | "virtual").
 * UI code filters/buckets by category as needed.
 */
export interface ExplorerFieldsResponse {
  entries: FieldCatalogEntry[];
}

export interface FieldValuesParams {
  field: string;
  limit?: number;
  search?: string;
  filters?: string;
}

export interface FieldValuesResponse {
  field: string;
  values: { value: string; count: number }[];
  total: number;
}

export interface SynonymLookup {
  [key: string]: string | string[];
}

export interface SemanticSuggestion {
  field: string;
  score: number;
  source?: string;
}

export interface AggregateParams {
  row_field: string;
  col_field?: string;
  filters?: string;
}

export interface AggregateResponse {
  rows: string[];
  columns: string[];
  data: Record<string, Record<string, number>>;
  grand_total: number;
}

export interface AssetHistoryParams {
  time_range?: string;
  limit?: number;
}

export interface AssetHistoryResponse {
  asset_id: string;
  events: {
    timestamp: string;
    field: string;
    old_value: any;
    new_value: any;
    source: string;
  }[];
  total: number;
}

export interface ClassifyRequest {
  category: string;
  subcategory: string;
}

export interface TagRequest {
  tag_name: string;
  tag_color?: string;
}

/** One manual tag carried by the filtered set, with how many assets carry it.
 *  Sourced server-side from asset_manual_tags only — rule-applied tags cannot
 *  be removed, so they are never offered. */
export interface TagBulkCandidate {
  tag_name: string;
  count: number;
}

export interface MergeRequest {
  primary_asset_id: string;
  secondary_asset_id: string;
  merge_strategy: string;
}

export interface UnmergeRequest {
  secondary_asset_id: string;
}

export interface AssetLabelOverrideRequest {
  label: string;
}

export interface AssetLabelOverrideResponse {
  label: string | null;
  previous_label?: string | null;
  overridden_by?: string;
  updated_at?: string;
}

/** Category-override state behind the Reclassify tab's "manually reclassified"
 *  indicator (#1155).
 *
 *  `is_overridden` is answered server-side from `category_overrides.active` —
 *  the durable record — and NOT from `assets.category_is_overridden`, which the
 *  pipeline resets to its default on every run and which therefore reads false
 *  for almost every overridden asset (#1270).
 *
 *  The `*_predicted` pair is what "Restore predicted" puts back. Both are null
 *  on assets last written before migration 143: restoring then only drops the
 *  override, leaving the live values for the next pipeline run to recompute. */
export interface CategoryOverrideResponse {
  asset_id: string;
  is_overridden: boolean;
  overridden_by: string | null;
  overridden_at: string | null;
  reason: string | null;
  category_predicted: string | null;
  subcategory_predicted: string | null;
}

/** `cleared` is false when the asset carried no override — the DELETE is
 *  idempotent, so "nothing to undo" is a success, not an error. */
export interface ClearCategoryOverrideResponse {
  asset_id: string;
  cleared: boolean;
}

/** Query params for the bulk-untag picker. Unlike its POST siblings this is a
 *  GET, so the filter array travels JSON-encoded in the query string (the shape
 *  /assets/values already takes). Shared by the raw call and the hook so the
 *  encoding lives in one place. */
function tagBulkCandidateParams(query: { filters?: unknown[]; search?: string }) {
  return {
    filters: JSON.stringify(query.filters ?? []),
    ...(query.search ? { search: query.search } : {}),
  };
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const assetsApi = {
  list: (params?: AssetListParams) =>
    apiClient.get<AssetListResponse>('/assets', { params: params as Record<string, any> }),

  get: (id: string) =>
    apiClient.get<Asset>('/assets/' + id),

  getFields: () =>
    apiClient.get<ExplorerFieldsResponse>('/assets/fields'),

  getFieldValues: (params: FieldValuesParams, opts?: { signal?: AbortSignal }) =>
    apiClient.get<FieldValuesResponse>('/assets/values', {
      params: params as Record<string, any>,
      signal: opts?.signal,
    }),

  search: (params: AssetSearchParams) =>
    apiClient.get<AssetSearchResponse>('/assets/search', { params: params as Record<string, any> }),

  aggregate: (params: AggregateParams) =>
    apiClient.get<AggregateResponse>('/assets/aggregate', { params: params as Record<string, any> }),

  exportAssetsCsv: (query: {
    filters?: unknown;
    search?: string | null;
    sort_by?: string | null;
    sort_dir?: string | null;
    fields?: string | null;
    logic?: string | null;
  }): Promise<{ blob: Blob; filename: string }> => {
    const params = new URLSearchParams();
    if (query.filters !== undefined && query.filters !== null) {
      params.set(
        'filters',
        typeof query.filters === 'string' ? query.filters : JSON.stringify(query.filters),
      );
    }
    if (query.search) params.set('search', query.search);
    if (query.sort_by) params.set('sort_by', query.sort_by);
    if (query.sort_dir) params.set('sort_dir', query.sort_dir);
    // `fields` makes the CSV columns == the columns the user picked (column-
    // driven export); `logic` keeps the and/or filter combination in sync.
    if (query.fields) params.set('fields', query.fields);
    if (query.logic) params.set('logic', query.logic);
    const qs = params.toString();
    return apiClient.download('/assets/export.csv' + (qs ? `?${qs}` : ''));
  },

  // Download an aggregate (pivot) matrix as CSV — the full row×col matrix incl
  // totals (export_assets target='aggregate', meta.layout 'aggregate').
  exportAggregateCsv: (query: {
    row_field?: string | null;
    col_field?: string | null;
    agg?: string | null;
    metric_field?: string | null;
    filters?: unknown;
    logic?: string | null;
    show?: string | null;
  }): Promise<{ blob: Blob; filename: string }> => {
    const params = new URLSearchParams();
    if (query.row_field) params.set('row_field', query.row_field);
    if (query.col_field) params.set('col_field', query.col_field);
    if (query.agg) params.set('agg', query.agg);
    if (query.metric_field) params.set('metric_field', query.metric_field);
    if (query.filters !== undefined && query.filters !== null) {
      params.set(
        'filters',
        typeof query.filters === 'string' ? query.filters : JSON.stringify(query.filters),
      );
    }
    if (query.logic) params.set('logic', query.logic);
    if (query.show) params.set('show', query.show);
    const qs = params.toString();
    return apiClient.download('/assets/aggregate.csv' + (qs ? `?${qs}` : ''));
  },

  getHistory: (id: string, params?: AssetHistoryParams) =>
    apiClient.get<AssetHistoryResponse>('/assets/' + id + '/history', { params: params as Record<string, any> }),

  // Edit operations
  classify: (id: string, data: ClassifyRequest) =>
    apiClient.put<Record<string, any>>('/assets/' + id + '/classify', data),

  getCategories: () =>
    apiClient.get<{ categories: Array<{ name: string; subcategories: string[] }> }>('/assets/categories'),

  addCustomSubcategory: (category: string, name: string) =>
    apiClient.post<{ category: string; subcategory: string; created_by: string }>(
      '/assets/categories/' + encodeURIComponent(category) + '/subcategories',
      { name },
    ),

  classifyBulk: (data: { filters?: unknown[]; search?: string; category: string; subcategory: string }) =>
    apiClient.post<{ updated: number }>('/assets/classify-bulk', data),

  clearClassifyBulk: (data: { filters?: unknown[]; search?: string }) =>
    apiClient.post<{ cleared: number }>('/assets/classify-bulk/clear', data),

  tagBulk: (data: { filters?: unknown[]; search?: string; tag_name: string; tag_color: string }) =>
    apiClient.post<{ tagged: number; created: number }>('/assets/tags-bulk', data),

  tagBulkCandidates: (query: { filters?: unknown[]; search?: string }) =>
    apiClient.get<{ candidates: TagBulkCandidate[] }>('/assets/tags-bulk/candidates', {
      params: tagBulkCandidateParams(query),
    }),

  // Removal keys on the name alone — there is no colour to send.
  untagBulk: (data: { filters?: unknown[]; search?: string; tag_name: string }) =>
    apiClient.post<{ removed: number }>('/assets/tags-bulk/remove', data),

  getTags: (id: string) =>
    apiClient.get<{ tags: Record<string, any>[] }>('/assets/' + id + '/tags'),

  addTag: (id: string, data: TagRequest) =>
    apiClient.post<Record<string, any>>('/assets/' + id + '/tags', data),

  removeTag: (assetId: string, tagId: string) =>
    apiClient.del<{ message: string }>('/assets/' + assetId + '/tags/' + tagId),

  merge: (data: MergeRequest) =>
    apiClient.post<Record<string, any>>('/assets/merge', data),

  unmerge: (id: string, data: UnmergeRequest) =>
    apiClient.post<Record<string, any>>('/assets/' + id + '/unmerge', data),

  getMergeHistory: (id: string) =>
    apiClient.get<{ merge_history: Record<string, any>[] }>('/assets/' + id + '/merge-history'),

  getMergePreview: (id: string, targetId: string) =>
    apiClient.get<Record<string, unknown>>('/assets/' + id + '/merge-preview', { params: { target_id: targetId } }),

  getLabelOverride: (id: string) =>
    apiClient.get<AssetLabelOverrideResponse>('/assets/' + id + '/label-override'),

  setLabelOverride: (id: string, data: AssetLabelOverrideRequest) =>
    apiClient.put<AssetLabelOverrideResponse>('/assets/' + id + '/label-override', data),

  clearLabelOverride: (id: string) =>
    apiClient.del<AssetLabelOverrideResponse>('/assets/' + id + '/label-override'),

  getCategoryOverride: (id: string) =>
    apiClient.get<CategoryOverrideResponse>('/assets/' + id + '/category-override'),

  clearCategoryOverride: (id: string) =>
    apiClient.del<ClearCategoryOverrideResponse>('/assets/' + id + '/category-override'),

  // Search / suggestions (different API paths)
  getSynonyms: () =>
    apiClient.get<SynonymLookup>('/search/synonyms'),

  getSemanticSuggestions: (query: string, limit = 20, minScore = 0.5) =>
    apiClient.get<SemanticSuggestion[]>('/suggestions', { params: { q: query, limit, min_score: minScore } }),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useAssets(params?: AssetListParams) {
  return useApiQuery<AssetListResponse>(
    ['assets', 'list', JSON.stringify(params)],
    '/assets',
    {
      params: params as Record<string, unknown>,
      placeholderData: (prev: unknown) => prev as AssetListResponse | undefined,
      retry: false,
      enabled: params !== undefined,
    },
  );
}

export function useAsset(id: string | undefined) {
  return useApiQuery<Asset>(
    ['assets', 'detail', id],
    '/assets/' + id,
    { enabled: !!id },
  );
}

export function useAssetLabelOverride(id: string | undefined) {
  return useApiQuery<AssetLabelOverrideResponse>(
    ['assets', 'label-override', id],
    '/assets/' + id + '/label-override',
    { enabled: !!id },
  );
}

export function useCategoryOverride(id: string | undefined) {
  return useApiQuery<CategoryOverrideResponse>(
    ['assets', 'category-override', id],
    '/assets/' + id + '/category-override',
    { enabled: !!id },
  );
}

/** Explorer field catalog.
 *
 * `includeFilterOnly` asks for fields that may only be filtered on (today: the
 * Alert field). They are off by default server-side, so every other caller is
 * safe without change. The flag is part of the query key: the two payloads
 * differ and must not share a cache entry.
 */
export function useExplorerFields(includeFilterOnly = false) {
  return useApiQuery<ExplorerFieldsResponse>(
    ['assets', 'fields', includeFilterOnly],
    '/assets/fields',
    {
      staleTime: 5 * 60 * 1000,
      params: includeFilterOnly ? { include_filter_only: true } : undefined,
    },
  );
}

export function useFieldValues(params: FieldValuesParams) {
  return useApiQuery<FieldValuesResponse>(
    ['assets', 'values', JSON.stringify(params)],
    '/assets/values',
    { params: params as Record<string, any>, enabled: !!params.field, staleTime: 30_000 },
  );
}

export function useAssetSearch(params: AssetSearchParams) {
  return useApiQuery<AssetSearchResponse>(
    ['assets', 'search', JSON.stringify(params)],
    '/assets/search',
    { params: params as Record<string, any>, enabled: !!params.q },
  );
}

export function useAssetAggregate(params: AggregateParams) {
  return useApiQuery<AggregateResponse>(
    ['assets', 'aggregate', JSON.stringify(params)],
    '/assets/aggregate',
    { params: params as Record<string, any>, enabled: !!params.row_field },
  );
}

export function useAssetHistory(id: string | undefined, params?: AssetHistoryParams) {
  return useApiQuery<AssetHistoryResponse>(
    ['assets', 'history', id, JSON.stringify(params)],
    '/assets/' + id + '/history',
    { params: params as Record<string, any>, enabled: !!id },
  );
}

export function useCategories() {
  return useApiQuery<{ categories: Array<{ name: string; subcategories: string[] }> }>(
    ['assets', 'categories'],
    '/assets/categories',
    { staleTime: 5 * 60 * 1000 },
  );
}

export function useAddCustomSubcategory(
  options?: Omit<
    UseMutationOptions<
      { category: string; subcategory: string; created_by: string },
      Error,
      { category: string; name: string }
    >,
    'mutationFn'
  >,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<
    { category: string; subcategory: string; created_by: string },
    Error,
    { category: string; name: string }
  >({
    mutationFn: ({ category, name }) => assetsApi.addCustomSubcategory(category, name),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets', 'categories'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useClassifyAsset(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, { id: string; data: ClassifyRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, { id: string; data: ClassifyRequest }>({
    mutationFn: ({ id, data }) => assetsApi.classify(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useClassifyBulk(
  options?: Omit<
    UseMutationOptions<
      { updated: number },
      Error,
      { filters?: unknown[]; search?: string; category: string; subcategory: string }
    >,
    'mutationFn'
  >,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<
    { updated: number },
    Error,
    { filters?: unknown[]; search?: string; category: string; subcategory: string }
  >({
    mutationFn: (data) => assetsApi.classifyBulk(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useClearClassifyBulk(
  options?: Omit<
    UseMutationOptions<{ cleared: number }, Error, { filters?: unknown[]; search?: string }>,
    'mutationFn'
  >,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ cleared: number }, Error, { filters?: unknown[]; search?: string }>({
    mutationFn: (data) => assetsApi.clearClassifyBulk(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useTagBulk(
  options?: Omit<
    UseMutationOptions<
      { tagged: number; created: number },
      Error,
      { filters?: unknown[]; search?: string; tag_name: string; tag_color: string }
    >,
    'mutationFn'
  >,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<
    { tagged: number; created: number },
    Error,
    { filters?: unknown[]; search?: string; tag_name: string; tag_color: string }
  >({
    mutationFn: (data) => assetsApi.tagBulk(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

/** Manual tags on the assets matching the given filter, for the bulk-untag
 *  picker. The server enforces the same cap as the removal itself, so an
 *  over-wide filter surfaces here as the action's own 400 — deterministic, so
 *  don't retry it. */
export function useTagBulkCandidates(query: { filters?: unknown[]; search?: string }) {
  const params = tagBulkCandidateParams(query);
  return useApiQuery<{ candidates: TagBulkCandidate[] }>(
    ['assets', 'tags-bulk', 'candidates', params.filters, params.search ?? ''],
    '/assets/tags-bulk/candidates',
    { params, retry: false },
  );
}

export function useUntagBulk(
  options?: Omit<
    UseMutationOptions<
      { removed: number },
      Error,
      { filters?: unknown[]; search?: string; tag_name: string }
    >,
    'mutationFn'
  >,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<
    { removed: number },
    Error,
    { filters?: unknown[]; search?: string; tag_name: string }
  >({
    mutationFn: (data) => assetsApi.untagBulk(data),
    onSuccess: (...args) => {
      // The ['assets'] prefix covers the candidates query too, so the counts
      // are re-read the next time the dialog opens.
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useAddTag(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, { assetId: string; data: TagRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, { assetId: string; data: TagRequest }>({
    mutationFn: ({ assetId, data }) => assetsApi.addTag(assetId, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useRemoveTag(
  options?: Omit<UseMutationOptions<{ message: string }, Error, { assetId: string; tagId: string }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ message: string }, Error, { assetId: string; tagId: string }>({
    mutationFn: ({ assetId, tagId }) => assetsApi.removeTag(assetId, tagId),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useMergeAssets(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, MergeRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, MergeRequest>({
    mutationFn: (data) => assetsApi.merge(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUnmergeAsset(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, { id: string; data: UnmergeRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, { id: string; data: UnmergeRequest }>({
    mutationFn: ({ id, data }) => assetsApi.unmerge(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useSetAssetLabelOverride(
  options?: Omit<UseMutationOptions<AssetLabelOverrideResponse, Error, { id: string; data: AssetLabelOverrideRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<AssetLabelOverrideResponse, Error, { id: string; data: AssetLabelOverrideRequest }>({
    mutationFn: ({ id, data }) => assetsApi.setLabelOverride(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useClearAssetLabelOverride(
  options?: Omit<UseMutationOptions<AssetLabelOverrideResponse, Error, { id: string }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<AssetLabelOverrideResponse, Error, { id: string }>({
    mutationFn: ({ id }) => assetsApi.clearLabelOverride(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

/** Restore one asset to its predicted category.
 *
 *  The ['assets'] prefix covers ['assets', 'category-override', id], so the
 *  banner and the restore button disappear on their own once the write lands —
 *  the tab never has to reconcile its own state with the server's. */
export function useClearCategoryOverride(
  options?: Omit<UseMutationOptions<ClearCategoryOverrideResponse, Error, { id: string }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<ClearCategoryOverrideResponse, Error, { id: string }>({
    mutationFn: ({ id }) => assetsApi.clearCategoryOverride(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['assets'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useSearchSynonyms() {
  return useApiQuery<SynonymLookup>(
    ['search', 'synonyms'],
    '/search/synonyms',
    { staleTime: 10 * 60 * 1000 },
  );
}

export function useMergePreview(assetId: string | undefined, targetId: string | undefined) {
  return useApiQuery<Record<string, unknown>>(
    ['assets', 'merge-preview', assetId, targetId],
    '/assets/' + assetId + '/merge-preview',
    { params: { target_id: targetId }, enabled: !!assetId && !!targetId },
  );
}

export function useMergeHistory(assetId: string | undefined) {
  return useApiQuery<{ merge_history: Record<string, any>[] }>(
    ['assets', 'merge-history', assetId],
    '/assets/' + assetId + '/merge-history',
    { enabled: !!assetId },
  );
}
