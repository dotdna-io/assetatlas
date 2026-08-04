// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery, useApiPost, useApiPut, useApiDelete } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface User {
  id: string;
  username: string;
  email: string;
  full_name: string | null;
  role: 'user' | 'admin' | 'super_admin';
  is_active: boolean;
  email_verified: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
  is_ldap_user: boolean;
  is_oidc_user: boolean;
  onboarding_pending: boolean;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface RefreshResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: Pick<User, 'id' | 'username' | 'email' | 'role'>;
}

export interface SessionConfig {
  session_timeout_minutes: number;
}

export interface UserListParams {
  page?: number;
  limit?: number;
  search?: string;
  role?: string;
  is_active?: boolean;
}

export interface UserListResponse {
  users: User[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export interface UserStatsResponse {
  total: number;
  active: number;
  inactive: number;
  by_role: Record<string, number>;
}

export interface CreateUserRequest {
  username: string;
  email: string;
  password?: string;       // optional when invite_only=true
  full_name?: string;
  role?: string;
  invite_only?: boolean;
}

export interface CreateUserAdminResponse {
  user: User;
  invite_url: string | null;
  invite_expires_at: string | null;
  email_sent: boolean;
}

export interface OnboardingValidateResponse {
  valid: boolean;
  username: string | null;
  email: string | null;
  requires_mfa: boolean;
}

export interface OnboardingCompleteRequest {
  token: string;
  new_password: string;
}

export interface OnboardingCompleteResponse {
  success: boolean;
  username: string;
  message: string;
}

export interface ResendInviteResponse {
  success: boolean;
  invite_url: string;
  invite_expires_at: string;
  email_sent: boolean;
}

export interface UpdateUserRequest {
  // is_active is intentionally absent — the API rejects it. Use
  // disableUser/enableUser (and the useToggleUser hook) instead, which
  // emits a dedicated user_disabled/user_enabled audit row.
  email?: string;
  full_name?: string;
  role?: string;
}

export interface ResetPasswordRequest {
  new_password: string;
  must_change_password?: boolean;
}

// MFA types
export interface MfaStatusResponse {
  enabled: boolean;
}

export interface MfaSetupRequest {
  /** Optional user-supplied label for the new method. Empty body is accepted. */
  label?: string;
}

export interface MfaSetupResponse {
  method_id: string;
  secret: string;
  provisioning_uri: string;
  label: string;
}

export interface MfaVerifyRequest {
  code: string;
}

export interface MfaVerifyResponse {
  enabled: boolean;
}

export interface MfaDisableRequest {
  password: string;
  code: string;
}

export interface MfaChallengeRequest {
  mfa_challenge_token: string;
  code: string;
}

// Account self-service (#612)
export interface AccountMfaMethod {
  id: string;
  type: string;
  label: string;
  enabled: boolean;
  created_at: string | null;
}

export interface AccountMfaListResponse {
  methods: AccountMfaMethod[];
}

export interface AccountMfaUpdateRequest {
  label: string;
}

export interface AccountProfileUpdateRequest {
  full_name?: string;
}

export interface AccountPasswordChangeRequest {
  current_password: string;
  new_password: string;
}

// Forgot-password TOTP (#613)
export interface ForgotPasswordLookupResponse {
  paths: string[];
}

export interface ForgotPasswordTotpRequest {
  username: string;
  code: string;
  new_password: string;
}

export interface LoginMfaRequiredResponse {
  requires_mfa: true;
  mfa_challenge_token: string;
  expires_in: number;
}

export type LoginResult = LoginResponse | LoginMfaRequiredResponse;

export function isMfaRequired(r: LoginResult): r is LoginMfaRequiredResponse {
  return (r as LoginMfaRequiredResponse).requires_mfa === true;
}

// OIDC / SSO types
export type SsoProviderKind =
  | 'entra'
  | 'okta'
  | 'google'
  | 'aws'
  | 'cyberark'
  | 'generic';

export interface SsoProviders {
  oidc: {
    enabled: boolean;
    provider_name: string;
    provider_kind: SsoProviderKind;
  };
}

export interface OidcLoginResponse {
  authorization_url: string;
}

export interface OidcExchangeRequest {
  code: string;
}

// OIDC discovery diagnostic types
export interface OidcTestDiscoveryResponse {
  success: boolean;
  issuer: string | null;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  jwks_uri: string | null;
  errors: string[];
}

// LDAP types
export interface LdapTestRequest {
  username: string;
  password: string;
}

export interface LdapTestResponse {
  success: boolean;
  user_dn: string | null;
  mapped_role: string | null;
  attributes: Record<string, unknown>;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const authApi = {
  login: (data: LoginRequest) =>
    apiClient.post<LoginResult>('/auth/login', data),

  logout: () =>
    apiClient.post<{ message: string }>('/auth/logout'),

  refresh: () =>
    apiClient.post<RefreshResponse>('/auth/refresh'),

  getSessionConfig: () =>
    apiClient.get<SessionConfig>('/auth/session-config'),

  getMe: () =>
    apiClient.get<User>('/auth/me'),

  // User management
  listUsers: (params?: UserListParams) =>
    apiClient.get<UserListResponse>('/auth/users', { params: params as Record<string, any> }),

  getUserStats: () =>
    apiClient.get<UserStatsResponse>('/auth/users/stats'),

  getUser: (id: string) =>
    apiClient.get<User>('/auth/users/' + id),

  createUser: (data: CreateUserRequest) =>
    apiClient.post<CreateUserAdminResponse>('/auth/users', data),

  resendInvite: (id: string) =>
    apiClient.post<ResendInviteResponse>('/auth/users/' + id + '/resend-invite'),

  // Public onboarding (no JWT required)
  validateOnboardingToken: (token: string) =>
    apiClient.get<OnboardingValidateResponse>('/auth/onboarding/validate', { params: { token } }),

  completeOnboarding: (data: OnboardingCompleteRequest) =>
    apiClient.post<OnboardingCompleteResponse>('/auth/onboarding/complete', data),

  updateUser: (id: string, data: UpdateUserRequest) =>
    apiClient.put<{ user: User; message: string }>('/auth/users/' + id, data),

  disableUser: (id: string) =>
    apiClient.patch<{ success: boolean; message: string }>('/auth/users/' + id + '/disable'),

  enableUser: (id: string) =>
    apiClient.patch<{ success: boolean; message: string }>('/auth/users/' + id + '/enable'),

  deleteUser: (id: string) =>
    apiClient.del<{ success: boolean; message: string }>('/auth/users/' + id),

  resetPassword: (id: string, data: ResetPasswordRequest) =>
    apiClient.post<{ success: boolean; message: string }>('/auth/users/' + id + '/password/reset', data),

  // Admin-initiated password reset (#614). Sends a password-reset email
  // and audit-logs as user_password_changed_by_admin (distinct from the
  // public requestPasswordReset which emits password_reset_request).
  adminPasswordReset: (id: string) =>
    apiClient.post<{ success: boolean; message: string; email_sent: boolean }>(
      '/auth/users/' + id + '/password-reset',
    ),

  // Admin force-password-change (#614). Sets must_change_password=true.
  forcePasswordChange: (id: string) =>
    apiClient.patch<{ success: boolean; message: string }>(
      '/auth/users/' + id + '/force-password-change',
      {},
    ),

  // Admin reset-MFA (#614). Deletes the user_mfa row; the next local
  // login forces enrollment.
  resetMfa: (id: string) =>
    apiClient.post<{ success: boolean; message: string }>(
      '/auth/users/' + id + '/reset-mfa',
    ),

  // Public password reset (no JWT required)
  requestPasswordReset: (email: string) =>
    apiClient.post<{ success: boolean; message: string }>('/auth/password-reset/request', { email }),

  verifyPasswordReset: (token: string, new_password: string) =>
    apiClient.post<{ success: boolean; message: string; username?: string }>('/auth/password-reset/verify', { token, new_password }),

  // MFA self-service
  getMfaStatus: () =>
    apiClient.get<MfaStatusResponse>('/auth/mfa/status'),

  setupMfa: () =>
    apiClient.post<MfaSetupResponse>('/auth/mfa/setup'),

  verifyMfa: (data: MfaVerifyRequest) =>
    apiClient.post<MfaVerifyResponse>('/auth/mfa/verify', data),

  disableMfa: (data: MfaDisableRequest) =>
    apiClient.post<{ success: boolean }>('/auth/mfa/disable', data),

  // MFA login (public — no JWT)
  mfaChallenge: (data: MfaChallengeRequest) =>
    apiClient.post<LoginResponse>('/auth/mfa/challenge', data),

  // LDAP test endpoint (admin-only)
  testLdap: (data: LdapTestRequest) =>
    apiClient.post<LdapTestResponse>('/auth/ldap/test', data),

  // OIDC discovery test (admin-only)
  testOidcDiscovery: () =>
    apiClient.get<OidcTestDiscoveryResponse>('/auth/oidc/test-discovery'),

  // OIDC / SSO
  getSsoProviders: () =>
    apiClient.get<SsoProviders>('/auth/sso-providers'),
  startOidcLogin: () =>
    apiClient.post<OidcLoginResponse>('/auth/oidc/login'),
  exchangeOidcCode: (data: OidcExchangeRequest) =>
    apiClient.post<LoginResponse>('/auth/oidc/exchange', data),

  forgotPasswordLookup: (username: string) =>
    apiClient.post<ForgotPasswordLookupResponse>('/auth/forgot-password/lookup', { username }),

  forgotPasswordTotp: (data: ForgotPasswordTotpRequest) =>
    apiClient.post<{ success: boolean }>('/auth/forgot-password/totp', data),
};

export const accountApi = {
  getMfa: () =>
    apiClient.get<AccountMfaListResponse>('/account/mfa'),

  setupTotp: (data?: MfaSetupRequest) =>
    apiClient.post<MfaSetupResponse>('/account/mfa/totp/setup', data ?? {}),

  verifyTotp: (data: MfaVerifyRequest) =>
    apiClient.post<MfaVerifyResponse>('/account/mfa/totp/verify', data),

  renameMethod: (methodId: string, data: AccountMfaUpdateRequest) =>
    apiClient.patch<{ success: boolean }>('/account/mfa/' + methodId, data),

  removeMethod: (methodId: string) =>
    apiClient.del<{ success: boolean }>('/account/mfa/' + methodId),

  updateProfile: (data: AccountProfileUpdateRequest) =>
    apiClient.patch<{ success: boolean; user?: User }>('/account/profile', data),

  changePassword: (data: AccountPasswordChangeRequest) =>
    apiClient.post<{ success: boolean }>('/account/password', data),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useSessionConfig() {
  return useApiQuery<SessionConfig>(
    ['auth', 'session-config'],
    '/auth/session-config',
    { staleTime: 5 * 60 * 1000 },
  );
}

export function useCurrentUser() {
  return useApiQuery<User>(['auth', 'me'], '/auth/me');
}

export function useLogin(
  options?: Omit<UseMutationOptions<LoginResult, Error, LoginRequest>, 'mutationFn'>,
) {
  return useMutation<LoginResult, Error, LoginRequest>({
    mutationFn: (data) => authApi.login(data),
    ...options,
  });
}

export function useLogout(
  options?: Omit<UseMutationOptions<{ message: string }, Error, void>, 'mutationFn'>,
) {
  return useMutation<{ message: string }, Error, void>({
    mutationFn: () => authApi.logout(),
    ...options,
  });
}

// ---------------------------------------------------------------------------
// User management hooks
// ---------------------------------------------------------------------------

export function useUsers(params?: UserListParams) {
  return useApiQuery<UserListResponse>(
    ['auth', 'users', JSON.stringify(params)],
    '/auth/users',
    { params: params as Record<string, any> },
  );
}

export function useUserStats() {
  return useApiQuery<UserStatsResponse>(['auth', 'users', 'stats'], '/auth/users/stats');
}

export function useUser(id: string | undefined) {
  return useApiQuery<User>(
    ['auth', 'users', id],
    '/auth/users/' + id,
    { enabled: !!id },
  );
}

export function useCreateUser(
  options?: Omit<UseMutationOptions<CreateUserAdminResponse, Error, CreateUserRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<CreateUserAdminResponse, Error, CreateUserRequest>({
    mutationFn: (data) => authApi.createUser(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'users'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useResendInvite(
  options?: Omit<UseMutationOptions<ResendInviteResponse, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<ResendInviteResponse, Error, string>({
    mutationFn: (userId) => authApi.resendInvite(userId),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'users'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useValidateOnboardingToken(token: string | null) {
  return useApiQuery<OnboardingValidateResponse>(
    ['auth', 'onboarding', 'validate', token ?? ''],
    '/auth/onboarding/validate',
    {
      params: { token: token ?? '' },
      enabled: !!token,
      retry: false,
      staleTime: 0,
    },
  );
}

export function useCompleteOnboarding(
  options?: Omit<UseMutationOptions<OnboardingCompleteResponse, Error, OnboardingCompleteRequest>, 'mutationFn'>,
) {
  return useMutation<OnboardingCompleteResponse, Error, OnboardingCompleteRequest>({
    mutationFn: (data) => authApi.completeOnboarding(data),
    ...options,
  });
}

export function useUpdateUser(
  id: string,
  options?: Omit<UseMutationOptions<{ user: User; message: string }, Error, UpdateUserRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ user: User; message: string }, Error, UpdateUserRequest>({
    mutationFn: (data) => authApi.updateUser(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'users'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useToggleUser(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, { id: string; enable: boolean }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ success: boolean; message: string }, Error, { id: string; enable: boolean }>({
    mutationFn: ({ id, enable }) => enable ? authApi.enableUser(id) : authApi.disableUser(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'users'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useDeleteUser(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ success: boolean; message: string }, Error, string>({
    mutationFn: (id) => authApi.deleteUser(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'users'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useResetPassword(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, { id: string; data: ResetPasswordRequest }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ success: boolean; message: string }, Error, { id: string; data: ResetPasswordRequest }>({
    mutationFn: ({ id, data }) => authApi.resetPassword(id, data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'users'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function usePasswordResetRequest(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, string>, 'mutationFn'>,
) {
  return useMutation<{ success: boolean; message: string }, Error, string>({
    mutationFn: (email) => authApi.requestPasswordReset(email),
    ...options,
  });
}

export function usePasswordResetVerify(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string; username?: string }, Error, { token: string; new_password: string }>, 'mutationFn'>,
) {
  return useMutation<{ success: boolean; message: string; username?: string }, Error, { token: string; new_password: string }>({
    mutationFn: ({ token, new_password }) => authApi.verifyPasswordReset(token, new_password),
    ...options,
  });
}

export function useAdminPasswordReset(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string; email_sent: boolean }, Error, string>, 'mutationFn'>,
) {
  return useMutation<{ success: boolean; message: string; email_sent: boolean }, Error, string>({
    mutationFn: (id) => authApi.adminPasswordReset(id),
    ...options,
  });
}

export function useForcePasswordChange(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<{ success: boolean; message: string }, Error, string>({
    mutationFn: (id) => authApi.forcePasswordChange(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'users'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}

export function useResetMfa(
  options?: Omit<UseMutationOptions<{ success: boolean; message: string }, Error, string>, 'mutationFn'>,
) {
  return useMutation<{ success: boolean; message: string }, Error, string>({
    mutationFn: (id) => authApi.resetMfa(id),
    ...options,
  });
}

export function useMfaStatus(enabled: boolean = true) {
  return useApiQuery<MfaStatusResponse>(
    ['auth', 'mfa', 'status'],
    '/auth/mfa/status',
    { enabled, staleTime: 30 * 1000 },
  );
}

export function useSetupMfa(
  options?: Omit<UseMutationOptions<MfaSetupResponse, Error, void>, 'mutationFn'>,
) {
  return useMutation<MfaSetupResponse, Error, void>({
    mutationFn: () => authApi.setupMfa(),
    ...options,
  });
}

export function useVerifyMfa(
  options?: Omit<UseMutationOptions<MfaVerifyResponse, Error, MfaVerifyRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<MfaVerifyResponse, Error, MfaVerifyRequest>({
    mutationFn: (data) => authApi.verifyMfa(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'mfa'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useDisableMfa(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, MfaDisableRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ success: boolean }, Error, MfaDisableRequest>({
    mutationFn: (data) => authApi.disableMfa(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'mfa'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useMfaChallenge(
  options?: Omit<UseMutationOptions<LoginResponse, Error, MfaChallengeRequest>, 'mutationFn'>,
) {
  return useMutation<LoginResponse, Error, MfaChallengeRequest>({
    mutationFn: (data) => authApi.mfaChallenge(data),
    ...options,
  });
}

export function useTestLdap(
  options?: Omit<UseMutationOptions<LdapTestResponse, Error, LdapTestRequest>, 'mutationFn'>,
) {
  return useMutation<LdapTestResponse, Error, LdapTestRequest>({
    mutationFn: (data) => authApi.testLdap(data),
    ...options,
  });
}

export function useTestOidcDiscovery(
  options?: Omit<UseMutationOptions<OidcTestDiscoveryResponse, Error, void>, 'mutationFn'>,
) {
  return useMutation<OidcTestDiscoveryResponse, Error, void>({
    mutationFn: () => authApi.testOidcDiscovery(),
    ...options,
  });
}

export function useSsoProviders() {
  return useApiQuery<SsoProviders>(
    ['auth', 'sso-providers'],
    '/auth/sso-providers',
    { staleTime: 5 * 60 * 1000 },
  );
}

export function useStartOidcLogin(
  options?: Omit<UseMutationOptions<OidcLoginResponse, Error, void>, 'mutationFn'>,
) {
  return useMutation<OidcLoginResponse, Error, void>({
    mutationFn: () => authApi.startOidcLogin(),
    ...options,
  });
}

export function useExchangeOidcCode(
  options?: Omit<UseMutationOptions<LoginResponse, Error, OidcExchangeRequest>, 'mutationFn'>,
) {
  return useMutation<LoginResponse, Error, OidcExchangeRequest>({
    mutationFn: (data) => authApi.exchangeOidcCode(data),
    ...options,
  });
}

// ---------------------------------------------------------------------------
// Account self-service hooks (#612, #613)
// ---------------------------------------------------------------------------

export function useAccountMfa() {
  return useApiQuery<AccountMfaListResponse>(
    ['account', 'mfa'],
    '/account/mfa',
    { staleTime: 30 * 1000 },
  );
}

export function useSetupTotpSelf(
  options?: Omit<UseMutationOptions<MfaSetupResponse, Error, MfaSetupRequest | void>, 'mutationFn'>,
) {
  return useMutation<MfaSetupResponse, Error, MfaSetupRequest | void>({
    mutationFn: (data) => accountApi.setupTotp(data || undefined),
    ...options,
  });
}

export function useRenameMfaMethod(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, { methodId: string; label: string }>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ success: boolean }, Error, { methodId: string; label: string }>({
    mutationFn: ({ methodId, label }) => accountApi.renameMethod(methodId, { label }),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['account', 'mfa'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useVerifyTotpSelf(
  options?: Omit<UseMutationOptions<MfaVerifyResponse, Error, MfaVerifyRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<MfaVerifyResponse, Error, MfaVerifyRequest>({
    mutationFn: (data) => accountApi.verifyTotp(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['account', 'mfa'] });
      qc.invalidateQueries({ queryKey: ['auth', 'mfa'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useRemoveMfaMethod(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ success: boolean }, Error, string>({
    mutationFn: (methodId) => accountApi.removeMethod(methodId),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['account', 'mfa'] });
      qc.invalidateQueries({ queryKey: ['auth', 'mfa'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useUpdateProfileSelf(
  options?: Omit<UseMutationOptions<{ success: boolean; user?: User }, Error, AccountProfileUpdateRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ success: boolean; user?: User }, Error, AccountProfileUpdateRequest>({
    mutationFn: (data) => accountApi.updateProfile(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'me'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useChangePasswordSelf(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, AccountPasswordChangeRequest>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ success: boolean }, Error, AccountPasswordChangeRequest>({
    mutationFn: (data) => accountApi.changePassword(data),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['auth', 'me'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useForgotPasswordLookup(
  options?: Omit<UseMutationOptions<ForgotPasswordLookupResponse, Error, string>, 'mutationFn'>,
) {
  return useMutation<ForgotPasswordLookupResponse, Error, string>({
    mutationFn: (username) => authApi.forgotPasswordLookup(username),
    ...options,
  });
}

export function useForgotPasswordTotp(
  options?: Omit<UseMutationOptions<{ success: boolean }, Error, ForgotPasswordTotpRequest>, 'mutationFn'>,
) {
  return useMutation<{ success: boolean }, Error, ForgotPasswordTotpRequest>({
    mutationFn: (data) => authApi.forgotPasswordTotp(data),
    ...options,
  });
}
