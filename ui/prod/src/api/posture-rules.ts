// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';
import type { FieldId } from '@/types/field-id';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const KNOWN_ASSET_CATEGORIES = [
  'server', 'workstation', 'mobile', 'network', 'iot',
  'ot', 'storage', 'miscellaneous', 'unknown',
] as const;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface FilterCondition {
  field_id: FieldId;
  operator: string;
  // List operators (any/all/none) and between carry a string[]; scalar/empty
  // ops carry a string. Legacy stored rules may still hold a comma string.
  value: string | string[];
}

export interface ComplianceCriteria {
  logic: 'all' | 'any';
  conditions: FilterCondition[];
}

export type PostureOutputMode = 'field' | 'text';

// JSONB dict stored in the `output_value` column. Matches backend
// sub_rule_management_service._serialise_output_value and
// PostureSubRuleResponse.output_value (Optional[Dict[str, Any]]).
export type PostureSubRuleOutput =
  | { mode: 'field'; field_id: FieldId }
  | { mode: 'text'; text: string };

export interface PostureSubRuleLastEdit {
  action: string;
  changed_by: string;
  changed_by_username: string | null;
  at: string;
}

export interface PostureSubRule {
  id: string;
  category_id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  target_filters: FilterCondition[];
  compliance_criteria: ComplianceCriteria;
  output_mode: PostureOutputMode | null;
  output_value: PostureSubRuleOutput | null;
  display_order: number;
  last_edit?: PostureSubRuleLastEdit | null;
  created_at: string;
  updated_at: string;
}

export interface AssetCategoryCoverage {
  covered: number;
  total: number;
}

export interface PostureCategoryStats {
  pass: number;
  fail: number;
  n_a: number;
  by_asset_category: Record<string, AssetCategoryCoverage>;
}

export interface PostureCategory {
  id: string;
  name: string;
  description: string;
  icon: string;
  applicable_categories: string[];
  display_order: number;
  last_evaluated_at: string | null;
  sub_rules: PostureSubRule[];
  stats: PostureCategoryStats;
}

export interface PostureCategoryListResponse {
  categories: PostureCategory[];
}

export interface SubRulePayload {
  name: string;
  description?: string | null;
  enabled?: boolean;
  target_filters: FilterCondition[];
  compliance_criteria: ComplianceCriteria;
  output_mode?: PostureOutputMode | null;
  // Required on the server when output_mode === 'field'.
  output_field_id?: FieldId | null;
  // Required on the server when output_mode === 'text'.
  output_text?: string | null;
}

export interface CategoryUpdatePayload {
  applicable_categories: string[];
}

export interface PosturePreviewRequest {
  target_filters: FilterCondition[];
  compliance_criteria: ComplianceCriteria;
}

export interface PosturePreviewResponse {
  matched: number;
  pass: number;
  fail: number;
}

export interface PostureOutputPreviewRequest {
  target_filters: FilterCondition[];
  compliance_criteria: ComplianceCriteria;
  output_mode: 'field' | 'text';
  // Required when output_mode === 'field'.
  output_field_id?: FieldId | null;
  // Required when output_mode === 'text'.
  output_text?: string | null;
}

export interface PostureOutputPreviewSample {
  hostname: string | null;
  output: string | null;
}

export interface PostureOutputPreviewResponse {
  matched: number;
  passed: number;
  samples: PostureOutputPreviewSample[];
}

export interface PostureExecuteResponse {
  updated_count: number;
  duration_ms: number;
  skipped?: boolean;
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const postureRulesApi = {
  list: () =>
    apiClient.get<PostureCategoryListResponse>('/posture-rules'),

  updateCategory: (categoryId: string, data: CategoryUpdatePayload) =>
    apiClient.patch<PostureCategory>('/posture-rules/categories/' + categoryId, data),

  createSubRule: (categoryId: string, data: SubRulePayload) =>
    apiClient.post<PostureSubRule>('/posture-rules/' + categoryId + '/sub-rules', data),

  updateSubRule: (subRuleId: string, data: SubRulePayload) =>
    apiClient.put<PostureSubRule>('/posture-rules/sub-rules/' + subRuleId, data),

  deleteSubRule: (subRuleId: string) =>
    apiClient.del<undefined>('/posture-rules/sub-rules/' + subRuleId),

  toggleSubRule: (subRuleId: string, enabled: boolean) =>
    apiClient.post<PostureSubRule>('/posture-rules/sub-rules/' + subRuleId + '/toggle', { enabled }),

  reorderSubRules: (categoryId: string, orderedIds: string[]) =>
    apiClient.put<void>(
      '/posture-rules/' + categoryId + '/sub-rules/order',
      { ordered_ids: orderedIds },
    ),

  preview: (data: PosturePreviewRequest) =>
    apiClient.post<PosturePreviewResponse>('/posture-rules/sub-rules/preview', data),

  previewOutput: (data: PostureOutputPreviewRequest) =>
    apiClient.post<PostureOutputPreviewResponse>('/posture-rules/sub-rules/preview-output', data),

  execute: () =>
    apiClient.post<PostureExecuteResponse>('/posture-rules/execute'),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function usePostureCategories() {
  return useApiQuery<PostureCategoryListResponse>(
    ['posture-rules', 'list'],
    '/posture-rules',
  );
}

export function useCreatePostureSubRule(
  options?: Omit<UseMutationOptions<PostureSubRule, Error, { categoryId: string; data: SubRulePayload }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<PostureSubRule, Error, { categoryId: string; data: SubRulePayload }>({
    mutationFn: ({ categoryId, data }) => postureRulesApi.createSubRule(categoryId, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['posture-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdatePostureSubRule(
  options?: Omit<UseMutationOptions<PostureSubRule, Error, { subRuleId: string; data: SubRulePayload }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<PostureSubRule, Error, { subRuleId: string; data: SubRulePayload }>({
    mutationFn: ({ subRuleId, data }) => postureRulesApi.updateSubRule(subRuleId, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['posture-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeletePostureSubRule(
  options?: Omit<UseMutationOptions<undefined, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<undefined, Error, string>({
    mutationFn: (subRuleId) => postureRulesApi.deleteSubRule(subRuleId),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['posture-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useTogglePostureSubRule(
  options?: Omit<UseMutationOptions<PostureSubRule, Error, { subRuleId: string; enabled: boolean }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<PostureSubRule, Error, { subRuleId: string; enabled: boolean }>({
    mutationFn: ({ subRuleId, enabled }) => postureRulesApi.toggleSubRule(subRuleId, enabled),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['posture-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function usePreviewPostureSubRule(
  options?: Omit<UseMutationOptions<PosturePreviewResponse, Error, PosturePreviewRequest>, 'mutationFn'>,
) {
  return useMutation<PosturePreviewResponse, Error, PosturePreviewRequest>({
    mutationFn: (data) => postureRulesApi.preview(data),
    ...options,
  });
}

export function usePreviewPostureOutput(
  options?: Omit<UseMutationOptions<PostureOutputPreviewResponse, Error, PostureOutputPreviewRequest>, 'mutationFn'>,
) {
  return useMutation<PostureOutputPreviewResponse, Error, PostureOutputPreviewRequest>({
    mutationFn: (data) => postureRulesApi.previewOutput(data),
    ...options,
  });
}

export function useExecutePostureRules(
  options?: Omit<UseMutationOptions<PostureExecuteResponse, Error, void>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<PostureExecuteResponse, Error, void>({
    mutationFn: () => postureRulesApi.execute(),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['posture-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdatePostureCategory(
  options?: Omit<UseMutationOptions<PostureCategory, Error, { categoryId: string; data: CategoryUpdatePayload }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<PostureCategory, Error, { categoryId: string; data: CategoryUpdatePayload }>({
    mutationFn: ({ categoryId, data }) => postureRulesApi.updateCategory(categoryId, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['posture-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useReorderPostureSubRules(
  options?: Omit<UseMutationOptions<void, Error, { categoryId: string; orderedIds: string[] }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<void, Error, { categoryId: string; orderedIds: string[] }>({
    mutationFn: ({ categoryId, orderedIds }) => postureRulesApi.reorderSubRules(categoryId, orderedIds),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['posture-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}
