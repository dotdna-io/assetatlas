// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';

export interface ChatSession {
  id: string;
  title: string;
  model: string;
  provider: string;
  created_at: string;
  updated_at: string;
}

export type ChatRole = 'user' | 'assistant' | 'tool' | 'system';

export interface ChatMessage {
  id: string;
  seq: number;
  role: ChatRole;
  content: string | null;
  tool_calls: unknown[] | null;
  input_tokens: number | null;
  output_tokens: number | null;
  created_at: string;
}

export interface CreateSessionResponse {
  session_id: string;
  title: string;
  created_at: string;
}

export interface ListSessionsResponse {
  sessions: ChatSession[];
  total: number;
  page: number;
  per_page: number;
}

export interface GetSessionResponse {
  session: ChatSession;
  messages: ChatMessage[];
}

export async function createSession(
  title?: string,
): Promise<CreateSessionResponse> {
  // Omitting the field lets the backend apply its single source of truth
  // for the default session title (see DEFAULT_SESSION_TITLE in
  // src/api/blueprints/chat/models.py).
  const body: { title?: string } = title === undefined ? {} : { title };
  return apiClient.post<CreateSessionResponse>('/chat/sessions', body);
}

export async function listSessions(
  opts: { page?: number; perPage?: number } = {},
): Promise<ListSessionsResponse> {
  const page = opts.page ?? 1;
  const perPage = opts.perPage ?? 20;
  return apiClient.get<ListSessionsResponse>('/chat/sessions', {
    params: { page, per_page: perPage },
  });
}

export async function getSession(id: string): Promise<GetSessionResponse> {
  return apiClient.get<GetSessionResponse>(`/chat/sessions/${id}`);
}

export async function renameSession(
  id: string,
  title: string,
): Promise<ChatSession> {
  return apiClient.patch<ChatSession>(`/chat/sessions/${id}`, { title });
}

export async function deleteSession(id: string): Promise<void> {
  await apiClient.del<void>(`/chat/sessions/${id}`);
}

export async function autotitleSession(
  id: string,
  userText: string,
): Promise<ChatSession> {
  return apiClient.post<ChatSession>(`/chat/sessions/${id}/autotitle`, {
    user_text: userText,
  });
}

export async function sendFeedback(
  messageId: string,
  rating: 1 | -1,
  comment?: string,
): Promise<void> {
  const body: { message_id: string; rating: 1 | -1; comment?: string } = {
    message_id: messageId,
    rating,
  };
  if (comment !== undefined) body.comment = comment;
  await apiClient.post<void>('/chat/feedback', body);
}

// ---------------------------------------------------------------------------
// Admin usage + audit (Phase 12)
// ---------------------------------------------------------------------------

export interface AdminUsageTotals {
  messages: number;
  sessions: number;
  tokens_input: number;
  tokens_output: number;
  active_users: number;
}

export interface AdminUsageTopUser {
  user_id: string;
  username: string | null;
  messages: number;
}

export interface AdminUsageByProvider {
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  messages: number;
}

export interface AdminUsageToolCall {
  tool_name: string;
  count: number;
  error_count: number;
  timeout_count: number;
  denied_count: number;
  avg_duration_ms: number;
}

export interface AdminUsageDownvote {
  message_id: string;
  session_id: string;
  user_id: string;
  username: string | null;
  comment: string | null;
  created_at: string;
}

export interface AdminUsageResponse {
  window_days: number;
  totals: AdminUsageTotals;
  top_users: AdminUsageTopUser[];
  by_provider: AdminUsageByProvider[];
  tool_calls: AdminUsageToolCall[];
  recent_downvotes: AdminUsageDownvote[];
}

export interface AdminAuditItem {
  message_id: string;
  session_id: string;
  user_id: string;
  username: string | null;
  role: ChatRole;
  content_preview: string;
  input_tokens: number | null;
  output_tokens: number | null;
  error: string | null;
  created_at: string;
}

export interface AdminAuditResponse {
  items: AdminAuditItem[];
  total: number;
  page: number;
  per_page: number;
}

export async function getAdminUsage(days = 7): Promise<AdminUsageResponse> {
  return apiClient.get<AdminUsageResponse>('/chat/admin/usage', {
    params: { days },
  });
}

export async function getAdminAudit(opts: {
  days?: number;
  page?: number;
  perPage?: number;
  userId?: string;
  role?: ChatRole;
} = {}): Promise<AdminAuditResponse> {
  const params: Record<string, string | number> = {
    days: opts.days ?? 7,
    page: opts.page ?? 1,
    per_page: opts.perPage ?? 50,
  };
  if (opts.userId) params.user_id = opts.userId;
  if (opts.role) params.role = opts.role;
  return apiClient.get<AdminAuditResponse>('/chat/admin/audit', { params });
}

// ---------------------------------------------------------------------------
// Admin chatbot settings (per-provider — issue #636)
// ---------------------------------------------------------------------------

export type ChatProvider = 'anthropic' | 'openai' | 'openai_compat' | 'aws_bedrock' | 'foundry';

export type FoundryProtocol = 'anthropic' | 'openai_chat' | 'openai_responses';

export type BedrockAuthMode = 'access_keys' | 'assume_role';

export interface ProviderConfig {
  provider: ChatProvider;
  model: string;
  api_key_preview: string;
  api_key_configured: boolean;
  base_url: string;
  prompt_cache: boolean;
  thinking_enabled: boolean;
  // Bedrock-only — empty/null for other providers.
  aws_region: string;
  auth_mode: BedrockAuthMode | null;
  access_key_id_preview: string;
  role_arn: string;
  external_id: string;
  base_access_key_id_preview: string;
  // Foundry-only.
  foundry_protocol: FoundryProtocol | null;
}

export interface AdminChatConfig {
  // null when the admin has disabled every provider (chat turned off).
  active_provider: ChatProvider | null;
  chat_enabled: boolean;
  providers: Record<ChatProvider, ProviderConfig>;
}

export interface ProviderConfigUpdate {
  model?: string;
  api_key?: string;
  base_url?: string;
  prompt_cache?: boolean;
  thinking_enabled?: boolean;
  // Bedrock-only.
  aws_region?: string;
  auth_mode?: BedrockAuthMode;
  access_key_id?: string;
  secret_access_key?: string;
  role_arn?: string;
  external_id?: string;
  base_access_key_id?: string;
  base_secret_access_key?: string;
  // Foundry-only.
  foundry_protocol?: FoundryProtocol;
}

export interface AdminChatConfigUpdate {
  // Send null to deactivate every provider (distinct from omitting it).
  active_provider?: ChatProvider | null;
  providers?: Partial<Record<ChatProvider, ProviderConfigUpdate>>;
}

export interface AdminChatTestRequest extends ProviderConfigUpdate {
  provider: ChatProvider;
}

export interface AdminChatTestResult {
  ok: boolean;
  detail: string;
  sample_text: string;
  provider: string;
  model: string;
  stop_reason?: string;
  input_tokens?: number;
  output_tokens?: number;
}

export async function getAdminChatConfig(): Promise<AdminChatConfig> {
  return apiClient.get<AdminChatConfig>('/chat/admin/config');
}

export async function updateAdminChatConfig(
  updates: AdminChatConfigUpdate,
): Promise<AdminChatConfig> {
  return apiClient.patch<AdminChatConfig>('/chat/admin/config', updates);
}

export async function testAdminChatConfig(
  request: AdminChatTestRequest,
): Promise<AdminChatTestResult> {
  return apiClient.post<AdminChatTestResult>(
    '/chat/admin/config/test', request,
  );
}

// ---------------------------------------------------------------------------
// AWS Bedrock — live model list
// ---------------------------------------------------------------------------

export interface BedrockModel {
  id: string;
  name: string;
  provider: string;
}

export interface BedrockModelsResponse {
  models: BedrockModel[];
  source: 'live' | 'fallback';
  error?: string;
}

export async function getBedrockModels(
  region: string,
): Promise<BedrockModelsResponse> {
  return apiClient.get<BedrockModelsResponse>('/chat/admin/bedrock/models', {
    params: { region },
  });
}
