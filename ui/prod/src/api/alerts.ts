// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AlertAsset {
  id: string;
  hostname: string | null;
  ip: string | null;
  model: string | null;
  label?: string | null;
}

export interface AlertSubject {
  id: string;
  label: string;
  sublabel: string | null;
}

export interface Alert {
  id: string;
  type: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  status: string;
  title: string;
  description: string | null;
  reason: string | null;
  source: string | null;
  asset_count: number;
  assets: AlertAsset[];
  assets_by_category: Record<string, any> | null;
  data_sources: string[];
  subject_type: string;                 // asset | integration | audit_event | pipeline_run | metric | system
  subjects?: AlertSubject[] | null;     // typed-list subject types only
  threshold: any;
  labels?: Record<string, string>;     // populated for source='prometheus'
  fingerprint?: string | null;          // populated for source='prometheus'
  evidence?: Record<string, any> | null; // populated for anomaly alerts (§584)
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  // Pipeline-run linkage (#906): the run that last (re)evaluated this alert.
  // Null for prometheus / non-pipeline system alerts (e.g. update-available);
  // present for pipeline-evaluated alerts incl. anomaly (source='system') ones.
  last_pipeline_run_id?: string | null;
}

export interface AlertsParams {
  time_range?: string;
  severity?: string;
  type?: string;
  status?: string;
  search?: string;
  asset_id?: string;   // exact server-side per-asset filter (#903/#908)
  page?: number;
  per_page?: number;
}

export interface AlertsSummary {
  total: number;
  by_severity: Record<string, number>;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
}

export interface AlertsResponse {
  alerts: Alert[];
  summary: AlertsSummary;
  timeline: Record<string, any>[];
  // Most recent *completed* pipeline run — the anchor #904 highlights against.
  // Null when no pipeline run has completed yet.
  latest_pipeline_run_id?: string | null;
  pagination: {
    page: number;
    per_page: number;
    total_items: number;
    total_pages: number;
  };
}

export interface AlertHistoryPoint {
  pipeline_run_id: string | null;
  status: string;
  asset_count: number;
  severity: string;
  created_at: string;
  started_at: string | null;   // pipeline_runs.started_at — the time axis
  completed_at: string | null;
}

export interface AlertHistoryResponse {
  alert_id: string;
  history: AlertHistoryPoint[]; // oldest → newest (right edge = latest run)
}

export interface AlertPatchRequest {
  status: 'acknowledged' | 'resolved';
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const alertsApi = {
  list: (params?: AlertsParams) =>
    apiClient.get<AlertsResponse>('/alerts', { params: params as Record<string, any> }),

  patch: (id: string, data: AlertPatchRequest) =>
    apiClient.put<Alert>('/alerts/' + id, data),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useAlerts(params?: AlertsParams) {
  return useApiQuery<AlertsResponse>(
    ['alerts', 'list', JSON.stringify(params)],
    '/alerts',
    { params: params as Record<string, any> },
  );
}

export function usePatchAlert(
  options?: Omit<UseMutationOptions<Alert, Error, { id: string; data: AlertPatchRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<Alert, Error, { id: string; data: AlertPatchRequest }>({
    mutationFn: ({ id, data }) => alertsApi.patch(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['alerts'] });
      qc.invalidateQueries({ queryKey: ['dashboard', 'alerts'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

// Per-alert firing history for the drilldown timechart (#905). Mounted lazily
// (only when a drilldown opens), so the query runs on mount — no manual gating.
export function useAlertHistory(alertId: string, days = 90) {
  return useApiQuery<AlertHistoryResponse>(
    ['alerts', 'history', alertId, days],
    `/alerts/${alertId}/history`,
    { params: { days }, enabled: !!alertId },
  );
}

// ---------------------------------------------------------------------------
// Alert Rules (configuration, not fired alerts)
// ---------------------------------------------------------------------------

export interface AlertRuleCondition {
  field: string;
  op: string;
  value: string | string[];
}

export interface AlertRuleComplianceCriteria {
  logic: 'all' | 'any';
  conditions: AlertRuleCondition[];
}

export interface AlertRuleThreshold {
  condition: 'falls_below' | 'exceeds';
  value: number;
  unit: 'percent' | 'number';
}

export interface AnomalyThreshold {
  severity?: 'critical' | 'high' | 'medium' | 'low';
  pct_medium?: number; pct_high?: number;
  min_fit_rows?: number; max_fit_rows?: number; fit_budget_seconds?: number; top_k_categories?: number;
  z_medium?: number; z_high?: number; z_critical?: number;
  min_count?: number; min_pct?: number; min_pp?: number; systemic_pct?: number;
  window_runs?: number; window_days?: number; min_runs?: number; min_samples?: number;
  min_seasonal_samples?: number; min_attrs?: number; min_flaps?: number; min_seen?: number;
  stability_days?: number; settle_min_days?: number; settle_max_days?: number;
}

export interface AlertRule {
  id: string;
  name: string;
  slug: string;
  description: string;
  type: 'security' | 'compliance' | 'operational' | 'data_quality';
  severity: 'critical' | 'high' | 'medium' | 'low';
  enabled: boolean;
  source: 'system' | 'user' | 'prometheus';
  query: string | null;
  target_filters: AlertRuleCondition[] | null;
  compliance_criteria: AlertRuleComplianceCriteria | null;
  threshold: AlertRuleThreshold | AnomalyThreshold | null;
  silence_duration: number | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  evaluation_type?: 'sql' | 'filter' | 'anomaly' | 'external';
  detector_key?: string | null;
}

export interface CreateAlertRulePayload {
  name: string;
  description?: string;
  type: AlertRule['type'];
  severity: AlertRule['severity'];
  target_filters: AlertRuleCondition[];
  compliance_criteria?: AlertRuleComplianceCriteria | null;
  threshold?: AlertRuleThreshold | null;
  silence_duration?: number | null;
  enabled?: boolean;
}

export type UpdateAlertRulePayload = Partial<CreateAlertRulePayload> & { enabled?: boolean };

export interface AlertRulesResponse {
  rules: AlertRule[];
}

export interface AlertRulePreviewRequest {
  target_filters: AlertRuleCondition[];
  compliance_criteria?: AlertRuleComplianceCriteria | null;
}

export interface AlertRulePreviewResponse {
  total_matching: number;
}

export function useAlertRules() {
  return useApiQuery<AlertRulesResponse>(['alert-rules', 'list'], '/alert-rules');
}

export function useCreateAlertRule(
  options?: Omit<UseMutationOptions<AlertRule, Error, CreateAlertRulePayload>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<AlertRule, Error, CreateAlertRulePayload>({
    mutationFn: (payload) => apiClient.post<AlertRule>('/alert-rules', payload),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['alert-rules'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useUpdateAlertRule(
  options?: Omit<UseMutationOptions<AlertRule, Error, { id: string; data: UpdateAlertRulePayload }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<AlertRule, Error, { id: string; data: UpdateAlertRulePayload }>({
    mutationFn: ({ id, data }) => apiClient.put<AlertRule>(`/alert-rules/${id}`, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['alert-rules'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useDeleteAlertRule() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => apiClient.del(`/alert-rules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert-rules'] }),
  });
}

export function useToggleAlertRule() {
  const qc = useQueryClient();
  return useMutation<AlertRule, Error, { id: string; enabled: boolean }>({
    mutationFn: ({ id, enabled }) => apiClient.patch<AlertRule>(`/alert-rules/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert-rules'] }),
  });
}

export function useUpdateAnomalyThreshold() {
  const qc = useQueryClient();
  return useMutation<AlertRule, Error, { id: string; data: AnomalyThreshold }>({
    mutationFn: ({ id, data }) => apiClient.patch<AlertRule>(`/alert-rules/${id}/anomaly-threshold`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert-rules'] }),
  });
}

export function useAlertRulePreview() {
  return useMutation<AlertRulePreviewResponse, Error, AlertRulePreviewRequest>({
    mutationFn: (payload) => apiClient.post<AlertRulePreviewResponse>('/alert-rules/preview', payload),
  });
}
