// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SettingsResponse {
  settings: Record<string, Record<string, any>>;
  categories: string[];
}

export interface CategorySettingsResponse {
  category: string;
  settings: Record<string, any>;
}

export interface SettingUpdateRequest {
  value: any;
  reason?: string;
}

export interface SettingUpdateResponse {
  message: string;
  category: string;
  key: string;
  value: any;
  reason: string;
}

export interface SettingsSchemaResponse {
  schema: Record<string, any>;
}

export interface FieldRegistryEntry {
  id: number;
  field_name: string;
  field_type: string;
  display_name: string;
  description: string;
  description_source: string;
  data_source: string | null;
  searchable: boolean;
  synonyms?: string[];
  updated_at: string | null;
}

export interface FieldRegistryResponse {
  fields: FieldRegistryEntry[];
  total: number;
}

export interface FieldRegistryUpdateRequest {
  description: string;
  display_name: string;
}

export interface FieldDescriptionsConfig {
  enabled: boolean;
  provider_available: boolean;
  active_provider: string;
}

export interface EnrichmentDataset {
  id: string;
  name: string;
  status: string;
  match_field: string;
  match_type: string;
  target_field: string;
  row_count: number;
  matched_count?: number | null;
  uploaded_by: string;
  created_at: string;
}

// ── Email settings ──────────────────────────────────────────────────────────

export type EmailSecurity = 'none' | 'starttls' | 'tls';

// '__SET__' means "a password is stored"; '' means no password. The server
// never emits ciphertext; this sentinel is the only signal the UI uses to
// decide whether to show the "(saved)" placeholder.
export type EmailPasswordSentinel = '' | '__SET__';

export interface EmailSettings {
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_security: EmailSecurity;
  smtp_username: string;
  smtp_password: EmailPasswordSentinel;
  from_address: string;
  from_display_name: string;
  reply_to_address: string;
}

export interface TestEmailResult {
  success: boolean;
  error?: string;
}

// Effective security values that depend on env-var fallbacks. The Settings
// UI uses this to pre-fill inputs whose DB row is empty so the operator
// sees the install's bootstrap default instead of a blank field.
export interface SecurityEffectiveResponse {
  public_base_url: {
    value: string;
    source: 'setting' | 'env' | 'none';
  };
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

/**
 * AI (LLM) admin actions call a possibly-slow chat provider — reasoning models
 * (e.g. Qwen3) spend ~35s+ per field "thinking" before answering. The default
 * 30s request timeout aborts them ("signal is aborted without reason"). 90s
 * clears a single field with headroom while staying under the ~100s gateway
 * (Cloudflare Tunnel) edge timeout. The bulk synonym rebuild can't fit any
 * client timeout, so it runs as a background job instead (see
 * useSynonymsRegenStatus), not on this longer-but-still-bounded budget.
 */
const LLM_ADMIN_TIMEOUT_MS = 90_000;

export interface SynonymsRegenStart {
  status: 'started' | 'already_running';
  message?: string;
}

export interface SynonymsRegenStatus {
  running: boolean;
  last_result: { ok?: boolean; message?: string; error?: string } | null;
}

export const settingsApi = {
  getAll: () =>
    apiClient.get<SettingsResponse>('/settings'),

  getSchema: () =>
    apiClient.get<SettingsSchemaResponse>('/settings/schema'),

  getCategory: (category: string) =>
    apiClient.get<CategorySettingsResponse>('/settings/' + category),

  getSetting: (category: string, key: string) =>
    apiClient.get<{ category: string; key: string; value: any }>('/settings/' + category + '/' + key),

  updateSetting: (category: string, key: string, data: SettingUpdateRequest) =>
    apiClient.put<SettingUpdateResponse>('/settings/' + category + '/' + key, data),

  resetSetting: (category: string, key: string, reason?: string) =>
    apiClient.post<SettingUpdateResponse>('/settings/' + category + '/' + key + '/reset', reason ? { reason } : undefined),

  // Field registry
  getFieldRegistry: () =>
    apiClient.get<FieldRegistryResponse>('/settings/field-registry'),

  updateField: (fieldName: string, data: FieldRegistryUpdateRequest) =>
    apiClient.put<Record<string, any>>('/settings/field-registry/' + fieldName, data),

  regenerateField: (fieldName: string) =>
    apiClient.post<Record<string, any>>(
      '/settings/field-registry/' + fieldName + '/regenerate',
      undefined,
      { timeout: LLM_ADMIN_TIMEOUT_MS },
    ),

  regenerateSynonyms: () =>
    apiClient.post<SynonymsRegenStart>('/settings/synonyms/regenerate'),

  getSynonymsRegenStatus: () =>
    apiClient.get<SynonymsRegenStatus>('/settings/synonyms/status'),

  getFieldDescriptionsConfig: () =>
    apiClient.get<FieldDescriptionsConfig>('/settings/field-descriptions'),

  setFieldDescriptionsEnabled: (enabled: boolean) =>
    apiClient.put<{ enabled: boolean }>('/settings/field-descriptions', { enabled }),

  // Enrichment
  listEnrichmentDatasets: () =>
    apiClient.get<{ datasets: EnrichmentDataset[] }>('/enrichment/datasets'),

  getEnrichmentDataset: (id: string) =>
    apiClient.get<{ dataset: EnrichmentDataset }>('/enrichment/datasets/' + id),

  deleteEnrichmentDataset: (id: string) =>
    apiClient.del<{ message: string }>('/enrichment/datasets/' + id),

  uploadEnrichmentDataset: (formData: FormData) =>
    apiClient.upload<Record<string, any>>('/enrichment/upload', formData),

  previewEnrichmentDataset: (id: string) =>
    apiClient.post<Record<string, any>>('/enrichment/datasets/' + id + '/preview'),

  applyEnrichmentDataset: (id: string) =>
    apiClient.post<Record<string, any>>('/enrichment/datasets/' + id + '/apply'),

  // ── Security ──────────────────────────────────────────────────────────────
  getSecurityEffective: () =>
    apiClient.get<SecurityEffectiveResponse>('/settings/security/effective'),

  // ── Email ─────────────────────────────────────────────────────────────────
  getEmail: () =>
    apiClient.get<EmailSettings>('/settings/email'),

  updateEmail: (key: keyof EmailSettings, value: unknown) =>
    apiClient.put<Record<string, unknown>>(
      '/settings/email/' + String(key),
      { value },
    ),

  sendTestEmail: (recipient: string) =>
    apiClient.post<TestEmailResult>('/settings/email/test', { recipient }),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useSettings() {
  return useApiQuery<SettingsResponse>(
    ['settings', 'all'],
    '/settings',
  );
}

export function useSettingsSchema() {
  return useApiQuery<SettingsSchemaResponse>(
    ['settings', 'schema'],
    '/settings/schema',
    { staleTime: 5 * 60 * 1000 },
  );
}

export function useCategorySettings(category: string | undefined) {
  return useApiQuery<CategorySettingsResponse>(
    ['settings', 'category', category],
    '/settings/' + category,
    { enabled: !!category },
  );
}

export function useUpdateSetting(
  options?: Omit<UseMutationOptions<SettingUpdateResponse, Error, { category: string; key: string; data: SettingUpdateRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<SettingUpdateResponse, Error, { category: string; key: string; data: SettingUpdateRequest }>({
    mutationFn: ({ category, key, data }) => settingsApi.updateSetting(category, key, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['settings', 'category', args[1].category] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useResetSetting(
  options?: Omit<UseMutationOptions<SettingUpdateResponse, Error, { category: string; key: string; reason?: string }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<SettingUpdateResponse, Error, { category: string; key: string; reason?: string }>({
    mutationFn: ({ category, key, reason }) => settingsApi.resetSetting(category, key, reason),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['settings', 'category', args[1].category] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

// Field registry hooks

export function useFieldRegistry() {
  return useApiQuery<FieldRegistryResponse>(
    ['settings', 'field-registry'],
    '/settings/field-registry',
  );
}

export function useUpdateFieldRegistry(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, { fieldName: string; data: FieldRegistryUpdateRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, { fieldName: string; data: FieldRegistryUpdateRequest }>({
    mutationFn: ({ fieldName, data }) => settingsApi.updateField(fieldName, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['settings', 'field-registry'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useRegenerateField(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, string>({
    mutationFn: (fieldName) => settingsApi.regenerateField(fieldName),
    onSuccess: (...args) => {
      // Invalidate the registry so the row's new description AND synonyms show.
      qc.invalidateQueries({ queryKey: ['settings', 'field-registry'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useRegenerateSynonyms(
  options?: Omit<UseMutationOptions<SynonymsRegenStart, Error, void>, 'mutationFn'>,
) {
  return useMutation<SynonymsRegenStart, Error, void>({
    mutationFn: () => settingsApi.regenerateSynonyms(),
    ...options,
  });
}

export function useSynonymsRegenStatus(enabled: boolean, runId = 0) {
  // runId is part of the query key so each regen cycle starts with a FRESH
  // cache entry — otherwise re-enabling within the app's 30s staleTime would
  // synchronously serve the previous cycle's cached `running: false` and fire
  // a false "done" before any refetch.
  return useApiQuery<SynonymsRegenStatus>(
    ['settings', 'synonyms-regen-status', runId],
    '/settings/synonyms/status',
    { enabled, refetchInterval: enabled ? 2000 : false },
  );
}

export function useFieldDescriptionsConfig() {
  return useApiQuery<FieldDescriptionsConfig>(
    ['settings', 'field-descriptions'],
    '/settings/field-descriptions',
  );
}

export function useSetFieldDescriptionsEnabled(
  options?: Omit<UseMutationOptions<{ enabled: boolean }, Error, boolean>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ enabled: boolean }, Error, boolean>({
    mutationFn: (enabled) => settingsApi.setFieldDescriptionsEnabled(enabled),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['settings', 'field-descriptions'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

// Enrichment hooks

export function useEnrichmentDatasets() {
  return useApiQuery<{ datasets: EnrichmentDataset[] }>(
    ['enrichment', 'datasets'],
    '/enrichment/datasets',
  );
}

export function useUploadEnrichmentDataset(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, FormData>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, FormData>({
    mutationFn: (formData) => settingsApi.uploadEnrichmentDataset(formData),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['enrichment'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useApplyEnrichmentDataset(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, string>({
    mutationFn: (id) => settingsApi.applyEnrichmentDataset(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['enrichment'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteEnrichmentDataset(
  options?: Omit<UseMutationOptions<{ message: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ message: string }, Error, string>({
    mutationFn: (id) => settingsApi.deleteEnrichmentDataset(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['enrichment'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useToggleEnrichmentDataset(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, { id: string; status: string }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, { id: string; status: string }>({
    mutationFn: ({ id, status }) => apiClient.put<Record<string, any>>('/enrichment/datasets/' + id + '/status', { status }),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['enrichment'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useEnrichmentExtFields() {
  return useApiQuery<{ fields: Record<string, any> }>(
    ['enrichment', 'ext-fields'],
    '/enrichment/ext-fields',
    { staleTime: 5 * 60 * 1000 },
  );
}

// Reliability scores hooks

export function useReliabilityScores() {
  return useApiQuery<Record<string, any>>(
    ['settings', 'reliability-scores'],
    '/settings/asset_generation/source_reliability_scores',
  );
}

export function useSaveReliabilityScores(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, Record<string, number>>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, Record<string, number>>({
    mutationFn: (scores) => apiClient.put<Record<string, any>>(
      '/settings/asset_generation/source_reliability_scores',
      { value: scores, reason: 'Updated from Settings UI' }
    ),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useResetReliabilityScores(
  options?: Omit<UseMutationOptions<Record<string, any>, Error, void>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Record<string, any>, Error, void>({
    mutationFn: () => apiClient.post<Record<string, any>>(
      '/settings/asset_generation/source_reliability_scores/reset',
      { reason: 'Reset from Settings UI' }
    ),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

// Health / About types

export interface HealthResponse {
  status: string;
  application?: {
    version?: string;
    uptime_seconds?: number;
    build_date?: string;
    features?: { chat?: boolean; monitoring?: boolean };
    [key: string]: any;
  };
  checks?: {
    database?: { status: string; [key: string]: any };
    cache?: { status: string; [key: string]: any };
    scheduler?: { status: string; jobs?: number; [key: string]: any };
    [key: string]: any;
  };
  [key: string]: any;
}

export interface HealthAboutResponse {
  assets?: { total: number; data_sources: number; last_pipeline_run: string | null };
  integrations?: {
    total: number;
    active: number;
    next_sync: { name: string; at: string } | null;
  };
  database?: { size: string; top_tables?: { name: string; rows: number }[] };
  redis?: { used_memory_human: string; peak_memory_human: string; connected_clients: number };
  runtime?: { python_version: string; platform: string };
  logs?: { total_size: string; file_count: number };
  [key: string]: any;
}

export interface UpdateCheckResponse {
  enabled: boolean;
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  checked_at: string | null;
}

// Health hooks

export function useHealth() {
  return useApiQuery<HealthResponse>(
    ['health'],
    '/health',
    { refetchInterval: 30_000 },
  );
}

export function useSuggestionsHealth() {
  return useApiQuery<Record<string, any>>(
    ['suggestions', 'health'],
    '/suggestions/health',
    { refetchInterval: 30_000, retry: 0 },
  );
}

export function useHealthAbout() {
  return useApiQuery<HealthAboutResponse>(
    ['health', 'about'],
    '/health/about',
    { refetchInterval: 30_000 },
  );
}

export function useUpdateCheck() {
  // Polled lazily (30-min staleTime, no interval) — the About tab's /health
  // and /health/about polls are 30s; the update check must NOT ride that
  // cadence (see the backend 12h cache + GitHub rate limit).
  return useApiQuery<UpdateCheckResponse>(
    ['health', 'update-check'],
    '/health/update-check',
    { staleTime: 30 * 60 * 1000 },
  );
}

export function useForceUpdateCheck(
  options?: Omit<UseMutationOptions<UpdateCheckResponse, Error, void>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<UpdateCheckResponse, Error, void>({
    mutationFn: () => apiClient.post<UpdateCheckResponse>('/health/update-check'),
    onSuccess: (...args) => {
      // Push the fresh result straight into the query the badge reads.
      qc.setQueryData(['health', 'update-check'], args[0]);
      onSuccess?.(...args);
    },
    ...rest,
  });
}

// Toggle the system.enable_update_check master switch (admin). Writes through
// the generic settings PUT, then invalidates the update-check query so the
// About tab re-reads state: enabling triggers a fresh latest-version fetch and
// reveals the badge/Check-now; disabling returns enabled:false and hides them.
export function useSetUpdateCheckEnabled(
  options?: Omit<UseMutationOptions<SettingUpdateResponse, Error, boolean>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<SettingUpdateResponse, Error, boolean>({
    mutationFn: (enabled) =>
      settingsApi.updateSetting('system', 'enable_update_check', {
        value: enabled,
        reason: 'Toggled from About tab',
      }),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['health', 'update-check'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

// Audit log types

export interface AuditLogEntry {
  id: string;
  timestamp?: string;
  created_at?: string;
  user_id?: string;
  username?: string;
  method: string;
  path?: string;
  endpoint?: string;
  status_code?: number;
  response_status?: number;
  ip_address?: string;
  client_ip?: string;
  duration_ms?: number;
  request_body?: any;
  response_summary?: any;
  // Semantic annotation columns (migration 144) — NULL on HTTP-only rows.
  category?: string;
  resource_type?: string;
  resource_id?: string;
  action?: string;
  details?: any;
}

export interface AuditLogResponse {
  entries: AuditLogEntry[];
  total: number;
  page: number;
  pages: number;
  per_page: number;
}

export interface AuditLogParams {
  page?: number;
  per_page?: number;
  start_date?: string;
  end_date?: string;
  username?: string;
  method?: string;
  path?: string;
  status_code?: string;
  category?: string;
  resource_type?: string;
  resource_id?: string;
  action?: string;
}

export function useAuditLog(params: AuditLogParams) {
  return useApiQuery<AuditLogResponse>(
    ['audit-log', JSON.stringify(params)],
    '/audit-trail',
    { params: params as Record<string, any> },
  );
}
