// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';

// Metrics-forwarding remote_write config admin API (#922 Phase 3). Backs
// Settings -> Monitoring -> Forwarding. Mirrors api/channels.ts: a typed api
// object over the { success, data } envelope (apiClient unwraps `data`).
//
// The remote_write secret is WRITE-ONLY. The read view never returns it — only a
// `has_secret` boolean. On save, omit `auth_secret` (or send it blank) to keep
// the stored value; `auth_type: 'none'` clears it. Source of truth:
// src/api/blueprints/metrics_forwarding.py + src/modules/metrics_forwarding/service.py.

export type ForwardingAuthType = 'none' | 'bearer' | 'basic';

/** Read view — the secret is masked to `has_secret`, never returned. */
export interface ForwardingConfig {
  endpoint_url: string | null;
  auth_type: ForwardingAuthType;
  username: string | null;
  external_labels: Record<string, string>;
  enabled: boolean;
  has_secret: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

/** Write payload. `auth_secret` is write-only: omit/blank keeps the stored secret. */
export interface ForwardingConfigUpdate {
  endpoint_url: string | null;
  auth_type: ForwardingAuthType;
  auth_secret?: string;
  username?: string | null;
  external_labels: Record<string, string>;
  enabled: boolean;
}

/** Best-effort agent POST /-/reload result, returned inside `apply`. */
export interface ForwardingReloadStatus {
  attempted: boolean;
  ok?: boolean;
  status_code?: number;
  detail?: string;
  error?: string;
}

/** What the save did to the on-disk agent config + reload — surfaced to the admin. */
export interface ForwardingApplyStatus {
  enabled: boolean;
  config_path?: string;
  config_written?: boolean;
  config_removed?: boolean;
  write_error?: string | null;
  remove_error?: string | null;
  reload?: ForwardingReloadStatus;
}

/** Save response: the read view plus the `apply` block. */
export interface ForwardingConfigSaveResult extends ForwardingConfig {
  apply?: ForwardingApplyStatus;
}

/** Best-effort endpoint reachability check — any HTTP response counts as reachable. */
export interface ForwardingTestResult {
  reachable: boolean;
  status_code?: number;
  detail?: string;
  error?: string;
}

export const metricsForwardingApi = {
  getConfig: () => apiClient.get<ForwardingConfig>('/metrics-forwarding/config'),
  saveConfig: (body: ForwardingConfigUpdate) =>
    apiClient.put<ForwardingConfigSaveResult>('/metrics-forwarding/config', body),
  testConnection: () =>
    apiClient.post<ForwardingTestResult>('/metrics-forwarding/config/test'),
};
