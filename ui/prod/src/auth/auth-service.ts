// SPDX-License-Identifier: AGPL-3.0-or-later
export type UserRole = 'user' | 'admin' | 'super_admin';

export interface User {
  id: string;
  username: string;
  email: string;
  role: UserRole;
  full_name: string;
  is_ldap_user: boolean;
  is_oidc_user: boolean;
  must_change_password: boolean;
}

interface AuthConfig {
  sessionTimeout: number;
  tokenCheckInterval: number;
  refreshBeforeExpiry: number;
}

export const config: AuthConfig = {
  sessionTimeout: 30 * 60 * 1000,
  tokenCheckInterval: 60 * 1000,
  refreshBeforeExpiry: 5 * 60 * 1000,
};

const STORAGE_KEYS = {
  accessToken: 'access_token',
  refreshToken: 'refresh_token',
  tokenExpiry: 'token_expiry',
  currentUser: 'current_user',
  intendedPath: 'intended_path',
} as const;

let tokenCheckTimer: ReturnType<typeof setInterval> | null = null;
let lastActivity: number = Date.now();

function getApiBase(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="base-path"]');
  const basePath = meta?.content?.replace(/\/$/, '') || '';
  return window.location.origin + basePath + '/api';
}

function updateLastActivity(): void {
  lastActivity = Date.now();
}

function startActivityTracking(): void {
  const events = ['mousedown', 'keydown', 'scroll', 'touchstart', 'click'];
  events.forEach((event) => {
    window.addEventListener(event, updateLastActivity, { passive: true });
  });
}

function stopActivityTracking(): void {
  const events = ['mousedown', 'keydown', 'scroll', 'touchstart', 'click'];
  events.forEach((event) => {
    window.removeEventListener(event, updateLastActivity);
  });
}

function startTokenCheckTimer(): void {
  stopTokenCheckTimer();
  tokenCheckTimer = setInterval(() => {
    checkAndRefreshToken();
  }, config.tokenCheckInterval);
}

function stopTokenCheckTimer(): void {
  if (tokenCheckTimer !== null) {
    clearInterval(tokenCheckTimer);
    tokenCheckTimer = null;
  }
}

async function checkAndRefreshToken(): Promise<void> {
  const expiryStr = localStorage.getItem(STORAGE_KEYS.tokenExpiry);
  if (!expiryStr) return;

  const expiry = parseInt(expiryStr, 10);
  const now = Date.now();
  const timeUntilExpiry = expiry - now;

  if (timeUntilExpiry <= 0) {
    await logout();
    return;
  }

  if (timeUntilExpiry <= config.refreshBeforeExpiry) {
    try {
      await refreshAccessToken();
    } catch {
      await logout();
    }
  }
}

export async function fetchSessionConfig(): Promise<void> {
  const token = getAccessToken();
  if (!token) return;

  try {
    const response = await fetch(`${getApiBase()}/auth/session-config`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    if (response.ok) {
      const data = await response.json();
      if (data.session_timeout_minutes) {
        config.sessionTimeout = data.session_timeout_minutes * 60 * 1000;
      }
    }
  } catch {
    // Session config fetch is best-effort
  }
}

export interface LoginSuccess {
  kind: 'success';
  user: User;
}

export interface LoginMfaChallenge {
  kind: 'mfa_required';
  mfaChallengeToken: string;
  expiresIn: number;
}

export interface LoginMfaEnrollmentRequired {
  kind: 'mfa_enrollment_required';
  mfaEnrollmentToken: string;
  expiresIn: number;
}

export type LoginResult =
  | LoginSuccess
  | LoginMfaChallenge
  | LoginMfaEnrollmentRequired;

function _persistAndStartSession(data: any): User {
  localStorage.setItem(STORAGE_KEYS.accessToken, data.access_token);
  localStorage.setItem(STORAGE_KEYS.refreshToken, data.refresh_token);
  const expiry = Date.now() + (data.expires_in || 3600) * 1000;
  localStorage.setItem(STORAGE_KEYS.tokenExpiry, expiry.toString());
  const user: User = data.user;
  localStorage.setItem(STORAGE_KEYS.currentUser, JSON.stringify(user));
  void fetchSessionConfig();
  lastActivity = Date.now();
  startActivityTracking();
  startTokenCheckTimer();
  return user;
}

export async function login(
  username: string,
  password: string
): Promise<LoginResult> {
  const response = await fetch(`${getApiBase()}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || errorData.msg || 'Login failed');
  }

  const data = await response.json();

  if (data.requires_mfa === true) {
    return {
      kind: 'mfa_required',
      mfaChallengeToken: data.mfa_challenge_token,
      expiresIn: data.expires_in,
    };
  }

  if (data.requires_mfa_enrollment === true) {
    return {
      kind: 'mfa_enrollment_required',
      mfaEnrollmentToken: data.mfa_enrollment_token,
      expiresIn: data.expires_in,
    };
  }

  return { kind: 'success', user: _persistAndStartSession(data) };
}

export async function mfaEnrollSetup(
  mfaEnrollmentToken: string,
): Promise<{ secret: string; provisioning_uri: string }> {
  const response = await fetch(`${getApiBase()}/auth/mfa/enroll-setup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mfa_enrollment_token: mfaEnrollmentToken }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || errorData.msg || 'MFA setup failed');
  }
  return await response.json();
}

export async function mfaEnrollVerify(
  mfaEnrollmentToken: string,
  code: string,
): Promise<{ user: User }> {
  const response = await fetch(`${getApiBase()}/auth/mfa/enroll-verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mfa_enrollment_token: mfaEnrollmentToken, code }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(
      errorData.message || errorData.msg || 'MFA verification failed',
    );
  }

  const data = await response.json();
  const user = _persistAndStartSession(data);
  return { user };
}

export async function verifyMfaChallenge(
  mfaChallengeToken: string,
  code: string
): Promise<User> {
  const response = await fetch(`${getApiBase()}/auth/mfa/challenge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mfa_challenge_token: mfaChallengeToken, code }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || errorData.msg || 'MFA verification failed');
  }

  const data = await response.json();
  return _persistAndStartSession(data);
}

export async function exchangeOidcCode(code: string): Promise<User> {
  const response = await fetch(`${getApiBase()}/auth/oidc/exchange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const err: Error & { code?: string } = new Error(
      errorData.message || errorData.msg || 'OIDC sign-in failed',
    );
    err.code = errorData.error;
    throw err;
  }
  const data = await response.json();
  return _persistAndStartSession(data);
}

export async function logout(): Promise<void> {
  // Read tokens BEFORE clearing local state — the backend call needs them.
  const accessToken = getAccessToken();

  stopTokenCheckTimer();
  stopActivityTracking();

  // Default destination — used when the user wasn't OIDC-authenticated or the
  // operator hasn't configured RP-initiated logout.
  let nextUrl = '/login';

  if (accessToken) {
    try {
      // The backend's @jwt_required() expects an access token. Sending the
      // refresh token (the previous behavior) returned 401, so user_sessions
      // rows were never deactivated and no audit entry was written.
      // Awaited (not keepalive) because the response may carry oidc_logout_url
      // for RP-initiated single sign-out.
      const response = await fetch(`${getApiBase()}/auth/logout`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
        },
      });
      if (response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          oidc_logout_url?: string;
        };
        if (typeof body.oidc_logout_url === 'string' && body.oidc_logout_url) {
          nextUrl = body.oidc_logout_url;
        }
      }
    } catch {
      // Logout is best-effort — fall through to local-only with /login
    }
  }

  localStorage.removeItem(STORAGE_KEYS.accessToken);
  localStorage.removeItem(STORAGE_KEYS.refreshToken);
  localStorage.removeItem(STORAGE_KEYS.tokenExpiry);
  localStorage.removeItem(STORAGE_KEYS.currentUser);

  window.location.href = nextUrl;
}

export async function refreshAccessToken(): Promise<string> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }

  const response = await fetch(`${getApiBase()}/auth/refresh`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${refreshToken}`,
    },
  });

  if (!response.ok) {
    throw new Error('Token refresh failed');
  }

  const data = await response.json();

  localStorage.setItem(STORAGE_KEYS.accessToken, data.access_token);

  const expiry = Date.now() + (data.expires_in || 3600) * 1000;
  localStorage.setItem(STORAGE_KEYS.tokenExpiry, expiry.toString());

  return data.access_token;
}

export function getCurrentUser(): User | null {
  const userStr = localStorage.getItem(STORAGE_KEYS.currentUser);
  if (!userStr) return null;

  try {
    return JSON.parse(userStr) as User;
  } catch {
    return null;
  }
}

export async function fetchCurrentUser(): Promise<User | null> {
  const token = getAccessToken();
  if (!token) return null;

  try {
    const response = await fetch(`${getApiBase()}/auth/me`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    if (!response.ok) return null;

    const data = await response.json();
    const user: User = data.user || data;
    localStorage.setItem(STORAGE_KEYS.currentUser, JSON.stringify(user));
    return user;
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  const token = getAccessToken();
  if (!token) return false;

  const expiryStr = localStorage.getItem(STORAGE_KEYS.tokenExpiry);
  if (!expiryStr) return false;

  const expiry = parseInt(expiryStr, 10);
  return Date.now() < expiry;
}

export function hasRole(role: UserRole): boolean {
  const user = getCurrentUser();
  if (!user) return false;

  if (role === 'user') return true;
  if (role === 'admin')
    return user.role === 'admin' || user.role === 'super_admin';
  if (role === 'super_admin') return user.role === 'super_admin';

  return false;
}

export function isAdmin(): boolean {
  const user = getCurrentUser();
  if (!user) return false;
  return user.role === 'admin' || user.role === 'super_admin';
}

export function getAccessToken(): string | null {
  return localStorage.getItem(STORAGE_KEYS.accessToken);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(STORAGE_KEYS.refreshToken);
}

export function setIntendedPath(path: string): void {
  sessionStorage.setItem(STORAGE_KEYS.intendedPath, path);
}

export function getIntendedPath(): string | null {
  const path = sessionStorage.getItem(STORAGE_KEYS.intendedPath);
  if (path) {
    sessionStorage.removeItem(STORAGE_KEYS.intendedPath);
  }
  return path;
}

export function getLastActivity(): number {
  return lastActivity;
}

export function init(): void {
  if (isAuthenticated()) {
    lastActivity = Date.now();
    startActivityTracking();
    startTokenCheckTimer();
    fetchSessionConfig();
  }
}
