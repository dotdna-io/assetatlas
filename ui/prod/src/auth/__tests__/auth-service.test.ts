// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { config, fetchSessionConfig, logout } from '../auth-service';

const DEFAULT_SESSION_TIMEOUT_MS = 30 * 60 * 1000;

describe('fetchSessionConfig', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
    config.sessionTimeout = DEFAULT_SESSION_TIMEOUT_MS;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('applies session_timeout_minutes from the API as milliseconds', async () => {
    localStorage.setItem('access_token', 'test-token');
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_timeout_minutes: 15 }),
    }) as unknown as typeof fetch;

    await fetchSessionConfig();

    expect(config.sessionTimeout).toBe(15 * 60 * 1000);
  });

  it('leaves config unchanged when no token is stored', async () => {
    const fetchSpy = vi.fn();
    global.fetch = fetchSpy as unknown as typeof fetch;

    await fetchSessionConfig();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(config.sessionTimeout).toBe(DEFAULT_SESSION_TIMEOUT_MS);
  });

  it('leaves config unchanged when the response omits the timeout', async () => {
    localStorage.setItem('access_token', 'test-token');
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({}),
    }) as unknown as typeof fetch;

    await fetchSessionConfig();

    expect(config.sessionTimeout).toBe(DEFAULT_SESSION_TIMEOUT_MS);
  });
});

describe('logout', () => {
  const originalFetch = global.fetch;
  const originalLocation = window.location;

  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...originalLocation, href: '' },
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(window, 'location', {
      writable: true,
      value: originalLocation,
    });
    vi.restoreAllMocks();
  });

  it('sends the access token (not the refresh token) to /auth/logout', async () => {
    // Regression: logout previously sent the refresh token in the Authorization
    // header; the backend's @jwt_required() rejects refresh tokens with 401, so
    // user_sessions rows stayed is_active=true and no audit entry was written.
    localStorage.setItem('access_token', 'access-abc');
    localStorage.setItem('refresh_token', 'refresh-xyz');

    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchSpy as unknown as typeof fetch;

    await logout();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/auth\/logout$/);
    expect(init.method).toBe('POST');
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer access-abc');
    expect(headers.Authorization).not.toBe('Bearer refresh-xyz');
  });

  it('does not call /auth/logout when there is no access token', async () => {
    // No access token means we can't authenticate the request anyway; skip the
    // network call rather than firing a guaranteed-401.
    const fetchSpy = vi.fn();
    global.fetch = fetchSpy as unknown as typeof fetch;

    await logout();

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('redirects to /login when the response has no oidc_logout_url', async () => {
    localStorage.setItem('access_token', 'access-abc');
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'Logout successful' }),
    }) as unknown as typeof fetch;

    await logout();

    expect(window.location.href).toBe('/login');
  });

  it('redirects to oidc_logout_url for RP-initiated single sign-out', async () => {
    // OIDC users get an end_session_endpoint URL from the backend; following
    // it terminates the IdP session too, so the next sign-in prompts again.
    localStorage.setItem('access_token', 'access-abc');
    const idpLogoutUrl =
      'https://login.microsoftonline.com/t/oauth2/v2.0/logout' +
      '?post_logout_redirect_uri=https%3A%2F%2Fapp.example.com%2Flogin';
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'Logout successful', oidc_logout_url: idpLogoutUrl }),
    }) as unknown as typeof fetch;

    await logout();

    expect(window.location.href).toBe(idpLogoutUrl);
  });

  it('falls back to /login when the logout request fails', async () => {
    // Network error / 5xx — local logout still happens, just no IdP redirect.
    localStorage.setItem('access_token', 'access-abc');
    global.fetch = vi.fn().mockRejectedValue(new Error('network down')) as unknown as typeof fetch;

    await logout();

    expect(window.location.href).toBe('/login');
    // localStorage must still be cleared even if the backend call failed.
    expect(localStorage.getItem('access_token')).toBeNull();
  });
});
