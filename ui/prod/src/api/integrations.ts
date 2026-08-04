// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';
import type { CustomHttpConfig } from '@/pages/integration-wizard/wizard-state';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Integration {
  id: string;
  name: string;
  integration_type: string;
  status: 'enabled' | 'disabled' | 'error';
  config: Record<string, any>;
  field_mapping: Record<string, any>;
  transformations: Record<string, any>;
  data_filters: Record<string, any>[];
  category: string | null;
  logo: string | null;
  description: string | null;
  schedule_enabled: boolean;
  schedule_cron: string | null;
  schedule_timezone: string | null;
  next_run_at: string | null;
  is_running: boolean;
  source_method: string;
  group_id: string | null;
  created_at: string;
  updated_at: string;
  // Enriched fields from list endpoint
  latest_batch_records?: number;
  latest_batch_started_at?: string | null;
  latest_batch_status?: string | null;
  latest_batch_assets?: number;
  last_sync_at?: string | null;
  last_connection_test_status?: string | null;
  last_connection_test_message?: string | null;
}

export interface IntegrationListResponse {
  success: boolean;
  count: number;
  integrations: Integration[];
}

export interface IntegrationListParams {
  status?: string;
  integration_type?: string;
  exclude_manual?: boolean;
}

export interface IntegrationCreateRequest {
  name: string;
  integration_type: string;
  config: Record<string, any>;
  field_mapping?: Record<string, any>;
  transformations?: Record<string, any>;
  data_filters?: Record<string, any>[];
  data_source_category?: string;
  logo?: string;
  description?: string;
  source_method?: string;
  last_connection_test_status?: string;
  last_connection_test_message?: string;
}

export interface IntegrationUpdateRequest {
  name?: string;
  config?: Record<string, any>;
  field_mapping?: Record<string, any>;
  transformations?: Record<string, any>;
  data_filters?: Record<string, any>[];
  status?: string;
  category?: string;
  logo?: string;
  description?: string;
}

export interface Connector {
  integration_type: string;
  display_name: string;
  category: string;
  description: string;
  logo_url: string | null;
  config_schema: Record<string, any>;
  connector_role: 'technology' | 'transport';
  // Connector key when a direct integration exists for this tile.
  // Null for DB-only tiles that can only be ingested via SIEM/datalake.
  direct_connector?: string | null;
}

export interface ConnectorListResponse {
  success: boolean;
  count: number;
  connectors: Connector[];
}

export interface TestConnectionResponse {
  success: boolean;
  message: string;
  details?: Record<string, any>;
}

export interface CustomHttpTestResponse {
  success: boolean;
  status_code?: number;
  elapsed_ms?: number;
  response_headers?: Record<string, string>;
  body_preview?: unknown;
  body_truncated?: boolean;
  records_root_match?: {
    valid_path: boolean;
    matched_count?: number;
    sample_records?: unknown[];
    error?: string;
    // Set when records_root was empty and the connector auto-detected
    // the records list. detected_path is the JSONPath chosen (e.g. '$.items').
    auto_detected?: boolean;
    detected_path?: string | null;
  };
  error_type?: 'auth' | 'network' | 'http' | 'json' | 'jsonpath' | 'config';
  message?: string;
  errors?: string[];
}

export interface ScheduleInfo {
  enabled: boolean;
  cron: string | null;
  timezone: string | null;
  next_run_at: string | null;
  is_running: boolean;
  description?: string;
  next_5_runs?: string[];
}

export interface ScheduleUpdateRequest {
  cron: string;
  timezone?: string;
  enabled?: boolean;
}

export interface Batch {
  id: string;
  integration_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  total_records: number;
  records_mapped: number;
  assets_created: number;
  raw_records_count: number;
  mapped_records_count: number;
  assets_created_count: number;
  error_message: string | null;
  [key: string]: any;
}

export interface BatchListParams {
  limit?: number;
  offset?: number;
  status?: string;
}

export interface BatchListResponse {
  success: boolean;
  total: number;
  limit: number;
  offset: number;
  batches: Batch[];
}

export interface BatchRecordsParams {
  limit?: number;
  offset?: number;
}

export interface BatchRecordsResponse {
  success: boolean;
  total: number;
  limit: number;
  offset: number;
  records: Record<string, any>[];
}

export interface RawDataResponse {
  success: boolean;
  integration_id: string;
  batch_id: string | null;
  batch_completed_at: string | null;
  total_records: number;
  records: Record<string, any>[];
  field_schema: Record<string, any>[];
  limit: number;
  offset: number;
}

export interface MappingPreviewResponse {
  success: boolean;
  integration_id: string;
  batch_id: string | null;
  total_records: number;
  // Number of records matching the active status filter (== total_records
  // when status is 'all'); drives pagination of the filtered view.
  filtered_total: number;
  records: Record<string, any>[];
  dropped_count: number;
  columns: string[];
  source_map: Record<string, string>;
  transform_map: Record<string, string[]>;
  field_stats: Record<string, any>[];
  limit: number;
  offset: number;
}

export interface PreviewFetchRequest {
  integration_id: string;
  session_id: string;
  /**
   * Optional candidate config from the wizard. Same merge semantics as the
   * /test and /sample endpoints: non-sensitive keys override the saved row,
   * sensitive keys fall back to the saved decrypted value. When present, the
   * backend skips the DB-cache shortcut.
   */
  config?: Record<string, any>;
}

export interface PreviewFetchResponse {
  success: boolean;
  total_count: number;
  sample_count: number;
  samples: Record<string, any>[];
  session_id: string;
}

export interface PreviewFilterCountRequest {
  session_id: string;
  filters: { field: string; operator: string; value?: any }[];
}

export interface PreviewFilterCountResponse {
  success: boolean;
  match_count: number;
  total_count: number;
}

export interface PostureScores {
  visibility: number;
  quality: number;
  completeness: number;
}

export interface IntegrationInsights {
  success: boolean;
  integration_id: string;
  posture_scores?: PostureScores;
  [key: string]: any;
}

export interface IntegrationTimelinePoint {
  label: string;
  added: number;
  returned: number;
  removed: number;
}

export interface IntegrationTimeline {
  integration_id: string;
  range: string;
  granularity: string;
  data: IntegrationTimelinePoint[];
}


// ---------------------------------------------------------------------------
// Group types
// ---------------------------------------------------------------------------

export interface IntegrationGroupMember {
  id: string;
  name: string;
  logo: string | null;
  integration_type: string;
}

export interface IntegrationGroup {
  id: string;
  name: string;
  integration_type: string;
  logo: string | null;
  member_count: number;
  members?: IntegrationGroupMember[];
}

export interface GroupCreateRequest {
  name: string;
  integration_type: string;
}

export interface GroupUpdateRequest {
  name: string;
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const integrationsApi = {
  // CRUD
  list: (params?: IntegrationListParams) =>
    apiClient.get<IntegrationListResponse>('/integrations', { params: params as Record<string, any> }),

  get: (id: string, includeCredentials?: boolean) =>
    apiClient.get<{ success: boolean; integration: Integration }>('/integrations/' + id, {
      params: includeCredentials ? { include_credentials: true } : undefined,
    }),

  create: (data: IntegrationCreateRequest) =>
    apiClient.post<{ success: boolean; message: string; integration: Integration }>('/integrations', data),

  update: (id: string, data: IntegrationUpdateRequest) =>
    apiClient.put<{ success: boolean; message: string; integration: Integration }>('/integrations/' + id, data),

  delete: (id: string) =>
    apiClient.del<{ success: boolean; message: string }>('/integrations/' + id),

  // Connectors
  listAvailable: () =>
    apiClient.get<ConnectorListResponse>('/integrations/available'),

  getConnectorSchema: (type: string) =>
    apiClient.get<{ success: boolean; integration_type: string; schema: Record<string, any> }>(
      '/integrations/available/' + type + '/schema',
    ),

  // Operations
  testConnection: (id: string, config?: Record<string, any>) =>
    apiClient.post<TestConnectionResponse>(
      '/integrations/' + id + '/test',
      config !== undefined ? { config } : undefined,
    ),

  getSampleData: (id: string, limit?: number, config?: Record<string, any>) => {
    const body: Record<string, any> = {};
    if (limit !== undefined) body.limit = limit;
    if (config !== undefined) body.config = config;
    return apiClient.post<{ success: boolean; records: Record<string, any>[]; total: number }>(
      '/integrations/' + id + '/sample',
      Object.keys(body).length > 0 ? body : undefined,
    );
  },

  getFieldSchema: (id: string) =>
    apiClient.get<{ success: boolean; schema: Record<string, any> }>('/integrations/' + id + '/schema'),

  run: (id: string) =>
    apiClient.post<{ success: boolean; message: string; integration_id: string }>('/integrations/' + id + '/run'),

  enable: (id: string) =>
    apiClient.patch<{ success: boolean; message: string }>('/integrations/' + id + '/enable'),

  disable: (id: string) =>
    apiClient.patch<{ success: boolean; message: string }>('/integrations/' + id + '/disable'),

  // Stats & logos
  getStats: () =>
    apiClient.get<{ success: boolean; stats: Record<string, any> }>('/integrations/stats'),


  listLogos: () =>
    apiClient.get<{ success: boolean; logos: string[] }>('/integrations/logos'),

  // Insights & timeline
  getInsights: (id: string) =>
    apiClient.get<IntegrationInsights>('/integrations/' + id + '/insights'),

  getTimeline: (id: string) =>
    apiClient.get<IntegrationTimeline>('/integrations/' + id + '/timeline'),

  // Schedule
  getSchedule: (id: string) =>
    apiClient.get<{ success: boolean; schedule: ScheduleInfo }>('/integrations/' + id + '/schedule'),

  updateSchedule: (id: string, data: ScheduleUpdateRequest) =>
    apiClient.put<{ success: boolean; message: string; schedule: ScheduleInfo }>(
      '/integrations/' + id + '/schedule',
      data,
    ),

  enableSchedule: (id: string) =>
    apiClient.patch<{ success: boolean; message: string; next_run_at: string | null }>(
      '/integrations/' + id + '/schedule/enable',
    ),

  disableSchedule: (id: string) =>
    apiClient.patch<{ success: boolean; message: string }>('/integrations/' + id + '/schedule/disable'),

  triggerSchedule: (id: string) =>
    apiClient.post<{ success: boolean; message: string }>('/integrations/' + id + '/schedule/trigger'),

  getNextRuns: (id: string, count?: number) =>
    apiClient.get<{ success: boolean; integration_id: string; count: number; next_runs: string[] }>(
      '/integrations/' + id + '/schedule/next-runs',
      { params: count ? { count } : undefined },
    ),

  getSchedulerStatus: () =>
    apiClient.get<{ success: boolean; scheduler: Record<string, any> }>('/integrations/scheduler/status'),

  // Batches
  listBatches: (integrationId: string, params?: BatchListParams) =>
    apiClient.get<BatchListResponse>('/integrations/' + integrationId + '/batches', {
      params: params as Record<string, any>,
    }),

  getBatch: (integrationId: string, batchId: string) =>
    apiClient.get<{ success: boolean; batch: Batch }>('/integrations/' + integrationId + '/batches/' + batchId),

  deleteBatch: (integrationId: string, batchId: string) =>
    apiClient.del<{ success: boolean; message: string }>('/integrations/' + integrationId + '/batches/' + batchId),

  getBatchRecords: (integrationId: string, batchId: string, params?: BatchRecordsParams) =>
    apiClient.get<BatchRecordsResponse>(
      '/integrations/' + integrationId + '/batches/' + batchId + '/records',
      { params: params as Record<string, any> },
    ),

  getMappedRecords: (integrationId: string, batchId: string, params?: BatchRecordsParams) =>
    apiClient.get<BatchRecordsResponse>(
      '/integrations/' + integrationId + '/batches/' + batchId + '/mapped-records',
      { params: params as Record<string, any> },
    ),

  triggerBatchMapping: (integrationId: string, batchId: string) =>
    apiClient.post<{ success: boolean; [key: string]: any }>(
      '/integrations/' + integrationId + '/batches/' + batchId + '/map',
    ),

  remapBatch: (integrationId: string, batchId: string) =>
    apiClient.post<{ success: boolean; [key: string]: any }>(
      '/integrations/' + integrationId + '/batches/' + batchId + '/remap',
    ),

  getBatchStats: (integrationId: string) =>
    apiClient.get<{ success: boolean; stats: Record<string, any> }>(
      '/integrations/' + integrationId + '/stats/batches',
    ),

  getMappingStats: (integrationId: string) =>
    apiClient.get<{ success: boolean; stats: Record<string, any> }>(
      '/integrations/' + integrationId + '/mapping/stats',
    ),

  getGlobalBatchStats: () =>
    apiClient.get<{ success: boolean; [key: string]: any }>('/integrations/batches/stats'),

  // Raw data & mapping preview
  getRawData: (integrationId: string, params?: { limit?: number; offset?: number; include_schema?: boolean }) =>
    apiClient.get<RawDataResponse>('/integrations/' + integrationId + '/raw-data', {
      params: params as Record<string, any>,
    }),

  getMappingPreview: (integrationId: string, params?: { limit?: number; offset?: number; status?: 'all' | 'ok' | 'dropped' }) =>
    apiClient.get<MappingPreviewResponse>('/integrations/' + integrationId + '/mapping-preview', {
      params: params as Record<string, any>,
    }),

  // Preview (wizard)
  fetchPreview: (data: PreviewFetchRequest) =>
    apiClient.post<PreviewFetchResponse>('/integrations/preview/fetch', data),

  filterCount: (data: PreviewFilterCountRequest) =>
    apiClient.post<PreviewFilterCountResponse>('/integrations/preview/filter-count', data),

  deletePreview: (sessionId: string) =>
    apiClient.del<{ success: boolean }>('/integrations/preview/' + sessionId),

  // Groups
  listGroups: () =>
    apiClient.get<{ success: boolean; groups: IntegrationGroup[] }>('/integrations/groups'),

  createGroup: (data: GroupCreateRequest) =>
    apiClient.post<{ success: boolean; group: IntegrationGroup }>('/integrations/groups', data),

  getGroup: (groupId: string) =>
    apiClient.get<{ success: boolean; group: IntegrationGroup }>('/integrations/groups/' + groupId),

  updateGroup: (groupId: string, data: GroupUpdateRequest) =>
    apiClient.put<{ success: boolean; group: IntegrationGroup }>('/integrations/groups/' + groupId, data),

  deleteGroup: (groupId: string) =>
    apiClient.del<{ success: boolean }>('/integrations/groups/' + groupId),

  addGroupMembers: (groupId: string, integrationIds: string[]) =>
    apiClient.post<{ success: boolean }>('/integrations/groups/' + groupId + '/members', {
      integration_ids: integrationIds,
    }),

  removeGroupMember: (groupId: string, integrationId: string) =>
    apiClient.del<{ success: boolean }>(
      '/integrations/groups/' + groupId + '/members/' + integrationId,
    ),

  getGroupMapping: () =>
    apiClient.get<{ success: boolean; mapping: Record<string, string> }>('/integrations/groups/mapping'),

  getLogoMap: () =>
    apiClient.get<{ success: boolean; logoMap: Record<string, string> }>('/integrations/logo-map'),

  // Health & errors
  getHealth: (id: string) =>
    apiClient.get<{ success: boolean; status: string; last_successful_run: string | null; last_error: string | null; is_running: boolean }>(
      '/integrations/' + id + '/health',
    ),

  getRecentErrors: (id: string, limit?: number) =>
    apiClient.get<{ success: boolean; errors: { timestamp: string | null; message: string; batch_id: string | null }[] }>(
      '/integrations/' + id + '/recent-errors',
      { params: limit ? { limit } : undefined },
    ),

  // Custom HTTP connector. In edit mode pass integrationId so the backend can
  // fall back to the saved decrypted secret — the wizard only holds the
  // still-encrypted password loaded from the existing integration. (Clone mode
  // loads decrypted credentials up front, so it passes no id and needs no
  // merge.)
  testCustomHttp: (config: CustomHttpConfig, integrationId?: string | null) =>
    apiClient.post<CustomHttpTestResponse>(
      '/integrations/connectors/custom_http/test',
      integrationId ? { ...config, integration_id: integrationId } : config,
    ),
};

// ---------------------------------------------------------------------------
// Query hooks — CRUD & discovery
// ---------------------------------------------------------------------------

export function useIntegrations(params?: IntegrationListParams, queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<IntegrationListResponse>(
    ['integrations', 'list', params],
    '/integrations',
    { params: params as Record<string, any>, ...queryOptions },
  );
}

export function useIntegration(id: string | undefined, queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<{ success: boolean; integration: Integration }>(
    ['integrations', 'detail', id],
    '/integrations/' + id,
    { enabled: !!id, staleTime: 60_000, ...queryOptions },
  );
}

export function useAvailableConnectors() {
  return useApiQuery<ConnectorListResponse>(
    ['integrations', 'available'],
    '/integrations/available',
    { staleTime: 5 * 60 * 1000 },
  );
}

export function useConnectorSchema(type: string | undefined) {
  return useApiQuery<{ success: boolean; integration_type: string; schema: Record<string, any> }>(
    ['integrations', 'schema', type],
    '/integrations/available/' + type + '/schema',
    { enabled: !!type, staleTime: 5 * 60 * 1000 },
  );
}


export function useIntegrationStats() {
  return useApiQuery<{ success: boolean; stats: Record<string, any> }>(
    ['integrations', 'stats'],
    '/integrations/stats',
  );
}

// ---------------------------------------------------------------------------
// Mutation hooks — CRUD
// ---------------------------------------------------------------------------

export function useCreateIntegration(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string; integration: Integration }, Error, IntegrationCreateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: IntegrationCreateRequest) => integrationsApi.create(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdateIntegration(
  id: string,
  options?: Omit<UseMutationOptions<{ success: boolean; message: string; integration: Integration }, Error, IntegrationUpdateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: IntegrationUpdateRequest) => integrationsApi.update(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteIntegration(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (id: string) => integrationsApi.delete(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks — Operations
// ---------------------------------------------------------------------------

export function useRunIntegration(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string; integration_id: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (id: string) => integrationsApi.run(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useTestConnection(
  options?: Omit<UseMutationOptions<TestConnectionResponse, Error, string>, 'mutationFn'>,
) {
  return useMutation({
    mutationFn: (id: string) => integrationsApi.testConnection(id),
    ...options,
  });
}

export function useToggleIntegration(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, { id: string; enable: boolean }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: ({ id, enable }: { id: string; enable: boolean }) =>
      enable ? integrationsApi.enable(id) : integrationsApi.disable(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

// ---------------------------------------------------------------------------
// Query hooks — Schedule
// ---------------------------------------------------------------------------

export function useIntegrationSchedule(id: string | undefined, queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<{ success: boolean; schedule: ScheduleInfo }>(
    ['integrations', 'schedule', id],
    '/integrations/' + id + '/schedule',
    { enabled: !!id, ...queryOptions },
  );
}

export function useUpdateSchedule(
  id: string,
  options?: Omit<UseMutationOptions<{ success: boolean; message: string; schedule: ScheduleInfo }, Error, ScheduleUpdateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: ScheduleUpdateRequest) => integrationsApi.updateSchedule(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useSchedulerStatus() {
  return useApiQuery<{ success: boolean; scheduler: Record<string, any> }>(
    ['integrations', 'scheduler', 'status'],
    '/integrations/scheduler/status',
  );
}

// ---------------------------------------------------------------------------
// Query hooks — Batches
// ---------------------------------------------------------------------------

export function useBatches(integrationId: string | undefined, params?: BatchListParams, queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<BatchListResponse>(
    ['integrations', 'batches', integrationId, params],
    '/integrations/' + integrationId + '/batches',
    { params: params as Record<string, any>, enabled: !!integrationId, ...queryOptions },
  );
}

export function useBatch(integrationId: string | undefined, batchId: string | undefined) {
  return useApiQuery<{ success: boolean; batch: Batch }>(
    ['integrations', 'batches', integrationId, batchId],
    '/integrations/' + integrationId + '/batches/' + batchId,
    { enabled: !!integrationId && !!batchId },
  );
}

export function useGlobalBatchStats() {
  return useApiQuery<{ success: boolean; [key: string]: any }>(
    ['integrations', 'batches', 'stats'],
    '/integrations/batches/stats',
  );
}

// ---------------------------------------------------------------------------
// Query hooks — Raw data & mapping
// ---------------------------------------------------------------------------

export function useRawData(integrationId: string | undefined, params?: { limit?: number; offset?: number; include_schema?: boolean }) {
  return useApiQuery<RawDataResponse>(
    ['integrations', 'raw-data', integrationId, params],
    '/integrations/' + integrationId + '/raw-data',
    { params: params as Record<string, any>, enabled: !!integrationId },
  );
}

export function useMappingPreview(integrationId: string | undefined, params?: { limit?: number; offset?: number; status?: 'all' | 'ok' | 'dropped' }, queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<MappingPreviewResponse>(
    ['integrations', 'mapping-preview', integrationId, params],
    '/integrations/' + integrationId + '/mapping-preview',
    { params: params as Record<string, any>, enabled: !!integrationId, ...queryOptions },
  );
}

export function useIntegrationInsights(id: string | undefined) {
  return useApiQuery<IntegrationInsights>(
    ['integrations', 'insights', id],
    '/integrations/' + id + '/insights',
    { enabled: !!id },
  );
}

export function useIntegrationTimeline(
  id: string | undefined,
  days?: number,
) {
  return useApiQuery<IntegrationTimeline>(
    ['integrations', 'timeline', id, days],
    '/integrations/' + id + '/timeline',
    { params: days ? { days } : undefined, enabled: !!id },
  );
}

// ---------------------------------------------------------------------------
// Mutation hooks — Preview (wizard)
// ---------------------------------------------------------------------------

export function useFetchPreview(
  options?: Omit<UseMutationOptions<PreviewFetchResponse, Error, PreviewFetchRequest>, 'mutationFn'>,
) {
  return useMutation({
    mutationFn: (data: PreviewFetchRequest) => integrationsApi.fetchPreview(data),
    ...options,
  });
}

export function useFilterCount(
  options?: Omit<UseMutationOptions<PreviewFilterCountResponse, Error, PreviewFilterCountRequest>, 'mutationFn'>,
) {
  return useMutation({
    mutationFn: (data: PreviewFilterCountRequest) => integrationsApi.filterCount(data),
    ...options,
  });
}

// ---------------------------------------------------------------------------
// Query hooks — Groups
// ---------------------------------------------------------------------------

export function useIntegrationGroups(queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<{ success: boolean; groups: IntegrationGroup[] }>(
    ['integrations', 'groups'],
    '/integrations/groups',
    { ...queryOptions },
  );
}

export function useIntegrationGroup(groupId: string | undefined) {
  return useApiQuery<{ success: boolean; group: IntegrationGroup }>(
    ['integrations', 'groups', groupId],
    '/integrations/groups/' + groupId,
    { enabled: !!groupId },
  );
}

/** Map of integration_name -> group_name. Used by the dashboard to split
 *  data sources into grouped (rendered under their group badge) and
 *  ungrouped (rendered as standalone cards). */
export function useIntegrationGroupMapping(queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<{ success: boolean; mapping: Record<string, string> }>(
    ['integrations', 'groups', 'mapping'],
    '/integrations/groups/mapping',
    { ...queryOptions },
  );
}

/** Map of integration name -> logo URL/path, built by the backend from each
 *  integration's persisted `logo`. Lets the Explorer chip resolve a renamed or
 *  custom-named source's logo by name (user-readable; no admin required). */
export function useLogoMap(queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<{ success: boolean; logoMap: Record<string, string> }>(
    ['integrations', 'logo-map'],
    '/integrations/logo-map',
    { staleTime: 5 * 60 * 1000, ...queryOptions },
  );
}

// ---------------------------------------------------------------------------
// Mutation hooks — Groups
// ---------------------------------------------------------------------------

export function useCreateGroup(
  options?: Omit<UseMutationOptions<{ success: boolean; group: IntegrationGroup }, Error, GroupCreateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: GroupCreateRequest) => integrationsApi.createGroup(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdateGroup(
  groupId: string,
  options?: Omit<UseMutationOptions<{ success: boolean; group: IntegrationGroup }, Error, GroupUpdateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (data: GroupUpdateRequest) => integrationsApi.updateGroup(groupId, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteGroup(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onMutate, onError, onSettled, onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (groupId: string) => integrationsApi.deleteGroup(groupId),
    onMutate: async (groupId, ...rest) => {
      await qc.cancelQueries({ queryKey: ['integrations', 'groups'] });
      const prev = qc.getQueryData<{ success: boolean; groups: IntegrationGroup[] }>(['integrations', 'groups']);
      if (prev) {
        qc.setQueryData(['integrations', 'groups'], {
          ...prev,
          groups: prev.groups.filter(g => g.id !== groupId),
        });
      }
      const callerContext = await onMutate?.(groupId, ...rest);
      return { prev, callerContext };
    },
    onError: (err, groupId, context: any, ...rest) => {
      if (context?.prev) qc.setQueryData(['integrations', 'groups'], context.prev);
      onError?.(err, groupId, context?.callerContext, ...rest);
    },
    onSettled: (data, err, groupId, context: any, ...rest) => {
      qc.removeQueries({ queryKey: ['integrations', 'groups', groupId] });
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSettled?.(data, err, groupId, context?.callerContext, ...rest);
    },
    onSuccess: (...args) => {
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useAddGroupMembers(
  groupId: string,
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, string[]>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (integrationIds: string[]) => integrationsApi.addGroupMembers(groupId, integrationIds),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useRemoveGroupMember(
  groupId: string,
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation({
    mutationFn: (integrationId: string) => integrationsApi.removeGroupMember(groupId, integrationId),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

// ---------------------------------------------------------------------------
// Query hooks — Health & Errors
// ---------------------------------------------------------------------------

export function useIntegrationHealth(id: string | undefined, queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<{ success: boolean; status: string; last_successful_run: string | null; last_error: string | null; is_running: boolean }>(
    ['integrations', 'health', id],
    '/integrations/' + id + '/health',
    { enabled: !!id, ...queryOptions },
  );
}

export function useRecentErrors(id: string | undefined, limit?: number, queryOptions?: { refetchInterval?: number }) {
  return useApiQuery<{ success: boolean; errors: { timestamp: string | null; message: string; batch_id: string | null }[] }>(
    ['integrations', 'recent-errors', id],
    '/integrations/' + id + '/recent-errors',
    { params: limit ? { limit } : undefined, enabled: !!id, ...queryOptions },
  );
}
