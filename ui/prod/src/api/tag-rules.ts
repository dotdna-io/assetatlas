// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';
import type { FieldId, FieldCatalogEntry } from '@/types/field-id';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TagRuleCondition {
  field_id: FieldId;
  operator: string;
  // List operators (any/all/none) and between carry a string[]; scalar/empty
  // ops carry a string. Legacy stored rules may still hold a comma string.
  value: string | string[];
}

export interface ComplianceCriteria {
  logic: 'all' | 'any';
  conditions: TagRuleCondition[];
}

export interface DetectionConfig {
  enabled: boolean;
  condition: string;
  threshold: number;
  severity: string;
}

export interface TagRuleTags {
  pass?: string;
  fail?: string;
  label?: string;
  color?: string;
}

export interface TagRule {
  id: string;
  name: string;
  description: string;
  type: 'compliance' | 'classification';
  enabled: boolean;
  priority: number;
  target_filters: TagRuleCondition[];
  compliance_criteria: ComplianceCriteria | null;
  detection_config: DetectionConfig | null;
  tags: TagRuleTags;
  execution_count: number;
  last_executed_at: string | null;
  last_execution_assets_matched: number;
  last_execution_assets_passed: number | null;
  last_execution_assets_failed: number | null;
  created_at: string;
  last_modified: string;
}

/** After the API client unwraps `{ data, success }`, we receive the inner payload. */
export interface TagRulesListResponse {
  rules: TagRule[];
  pagination: {
    page: number;
    per_page: number;
    total_items: number;
    total_pages: number;
  };
}

/** After the API client unwraps `{ data, success }`, we receive the inner payload. */
export interface TagRuleResponse {
  rule: TagRule;
}

export interface TagRuleCreateRequest {
  name: string;
  description?: string;
  type: 'compliance' | 'classification';
  enabled?: boolean;
  target_filters: TagRuleCondition[];
  compliance_criteria?: ComplianceCriteria;
  detection_config?: DetectionConfig;
  tags: TagRuleTags;
}

export interface TagRuleUpdateRequest {
  name?: string;
  description?: string;
  target_filters?: TagRuleCondition[];
  compliance_criteria?: ComplianceCriteria;
  detection_config?: DetectionConfig | null;
  tags?: TagRuleTags;
}

export interface ReorderRequest {
  rule_ids: string[];
}

/** After the API client unwraps `{ data, success }`, we receive the inner payload. */
export interface ExecuteResponse {
  result: {
    assets_matched?: number;
    tags_removed?: number;
  };
}

/**
 * After the API client unwraps `{ data, success }`, we receive the inner payload.
 *
 * Backend `GET /api/tag-rules/fields` returns a flat list of catalog entries;
 * each entry carries a `category` discriminator ("target" | "advanced" |
 * "extended_attributes" | "tags" | "enrichment"). UI code filters/buckets
 * by category as needed.
 */
export interface FieldsResponse {
  entries: FieldCatalogEntry[];
}

export interface OperatorInfo {
  name: string;
  label: string;
}

/** After the API client unwraps `{ data, success }`, we receive the inner payload. */
export type OperatorsResponse = OperatorInfo[];

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const tagRulesApi = {
  list: () =>
    apiClient.get<TagRulesListResponse>('/tag-rules'),

  get: (id: string) =>
    apiClient.get<TagRuleResponse>('/tag-rules/' + id),

  create: (data: TagRuleCreateRequest) =>
    apiClient.post<TagRuleResponse>('/tag-rules', data),

  update: (id: string, data: TagRuleUpdateRequest) =>
    apiClient.put<TagRuleResponse>('/tag-rules/' + id, data),

  delete: (id: string) =>
    apiClient.del<{ success: boolean }>('/tag-rules/' + id),

  execute: (id: string) =>
    apiClient.post<ExecuteResponse>('/tag-rules/' + id + '/execute'),

  toggle: (id: string) =>
    apiClient.post<TagRuleResponse>('/tag-rules/' + id + '/toggle'),

  reorder: (data: ReorderRequest) =>
    apiClient.put<{ success: boolean }>('/tag-rules/reorder', data),

  getFields: () =>
    apiClient.get<FieldsResponse>('/tag-rules/fields'),

  getOperators: () =>
    apiClient.get<OperatorsResponse>('/tag-rules/operators'),

  preview: (data: { target_filters: TagRuleCondition[]; compliance_criteria?: ComplianceCriteria }) =>
    apiClient.post<{ total_matching: number; pass_count?: number; fail_count?: number }>('/tag-rules/preview', data),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useTagRules() {
  return useApiQuery<TagRulesListResponse>(
    ['tag-rules', 'list'],
    '/tag-rules',
  );
}

export function useTagRuleFields() {
  return useApiQuery<FieldsResponse>(
    ['tag-rules', 'fields'],
    '/tag-rules/fields',
    { staleTime: 5 * 60 * 1000 },
  );
}

export function useCreateTagRule(
  options?: Omit<UseMutationOptions<TagRuleResponse, Error, TagRuleCreateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<TagRuleResponse, Error, TagRuleCreateRequest>({
    mutationFn: (data) => tagRulesApi.create(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['tag-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useUpdateTagRule(
  options?: Omit<UseMutationOptions<TagRuleResponse, Error, { id: string; data: TagRuleUpdateRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<TagRuleResponse, Error, { id: string; data: TagRuleUpdateRequest }>({
    mutationFn: ({ id, data }) => tagRulesApi.update(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['tag-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteTagRule(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ success: boolean }, Error, string>({
    mutationFn: (id) => tagRulesApi.delete(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['tag-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useExecuteTagRule(
  options?: Omit<UseMutationOptions<ExecuteResponse, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<ExecuteResponse, Error, string>({
    mutationFn: (id) => tagRulesApi.execute(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['tag-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useToggleTagRule(
  options?: Omit<UseMutationOptions<TagRuleResponse, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<TagRuleResponse, Error, string>({
    mutationFn: (id) => tagRulesApi.toggle(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['tag-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useReorderTagRules(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, ReorderRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ success: boolean }, Error, ReorderRequest>({
    mutationFn: (data) => tagRulesApi.reorder(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['tag-rules'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}
