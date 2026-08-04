// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// '__SET__' means "a password is stored"; '' means no password. Mirrors
// the email settings convention.
export type TeamsPasswordSentinel = '' | '__SET__';

export interface TeamsSettings {
  enabled: boolean;
  bot_app_id: string;
  bot_app_password: TeamsPasswordSentinel;
  bot_app_tenant_id: string;
  // Empty = computed from security.public_base_url; non-empty = override.
  messaging_endpoint: string;
}

export interface MessagingEndpoint {
  url: string;
  https_ok: boolean;
  is_override: boolean;
}

export interface TestConnectionResult {
  ok: boolean;
  error?: string;
}

// Credentials to probe instead of the saved ones. Not Partial<TeamsSettings>:
// bot_app_password here is a real secret, not the redaction sentinel.
export interface TestConnectionRequest {
  bot_app_id?: string;
  bot_app_password?: string;
  bot_app_tenant_id?: string;
}

export type TeamsSettingsKey = keyof TeamsSettings;

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------

export const teamsApi = {
  /** Get all Teams settings. The bot_app_password field is redacted. */
  get: () =>
    apiClient.get<TeamsSettings>('/settings/teams'),

  /** Update a single Teams setting (mirrors `settingsApi.updateEmail`). */
  update: (key: TeamsSettingsKey, value: unknown) =>
    apiClient.put<Record<string, unknown>>(
      '/settings/teams/' + String(key),
      { value },
    ),

  /**
   * Compute the URL the operator pastes into Azure Bot's Configuration.
   * Returns https_ok:false when the public URL is not HTTPS — UI should
   * surface a warning in that case (Teams rejects http:// endpoints).
   * is_override:true when an explicit override is configured.
   */
  getMessagingEndpoint: () =>
    apiClient.get<MessagingEndpoint>('/settings/teams/messaging-endpoint'),

  /**
   * Probe Bot Framework auth via the bridge. Body fields are optional;
   * missing fields fall back to the saved settings server-side. Always
   * returns 200 — outcome is in body.ok.
   */
  testConnection: (override?: TestConnectionRequest) =>
    apiClient.post<TestConnectionResult>(
      '/settings/teams/test-connection',
      override ?? {},
    ),

  /**
   * Download the sideload-ready Teams app zip via fetch-with-JWT,
   * NOT a plain `<a href>` — the access token lives in localStorage,
   * which the browser doesn't include on anchor navigations, so a static
   * link returns 401 with "File wasn't available on site."
   */
  downloadManifest: () =>
    apiClient.download('/settings/teams/manifest.zip'),
};
