// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import LoginPage from '../LoginPage';

// Stub useAuth — tests provide per-test overrides via mockReturnValue
vi.mock('@/auth/useAuth', () => ({
  useAuth: vi.fn(),
}));
vi.mock('@/hooks', () => ({
  useDarkMode: () => ({ isDarkMode: false, toggle: vi.fn() }),
  useDocumentTitle: () => undefined,
}));
vi.mock('@/api/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/auth')>();
  return {
    ...actual,
    useSsoProviders: vi.fn(),
    useStartOidcLogin: vi.fn(),
  };
});

import { useAuth } from '@/auth/useAuth';
import { useSsoProviders, useStartOidcLogin } from '@/api/auth';

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/dashboard" element={<div>DASHBOARD</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function makeAuth(overrides: Record<string, unknown> = {}) {
  return {
    user: null,
    isAuthenticated: false,
    isAdmin: false,
    isLoading: false,
    login: vi.fn(),
    verifyMfaChallenge: vi.fn(),
    exchangeOidcCode: vi.fn(),
    logout: vi.fn(),
    ...overrides,
  };
}

/** Default SSO providers result: OIDC disabled */
function mockSsoDisabled() {
  (useSsoProviders as ReturnType<typeof vi.fn>).mockReturnValue({ data: { oidc: { enabled: false, provider_name: 'SSO' } } });
  (useStartOidcLogin as ReturnType<typeof vi.fn>).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
}

/** SSO providers result: OIDC enabled with given provider name */
function mockSsoEnabled(providerName = 'Entra ID') {
  (useSsoProviders as ReturnType<typeof vi.fn>).mockReturnValue({ data: { oidc: { enabled: true, provider_name: providerName } } });
  (useStartOidcLogin as ReturnType<typeof vi.fn>).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
}

describe('LoginPage — credentials step', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSsoDisabled();
  });

  it('renders username and password fields', () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth());
    renderLogin();
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('shows error when only whitespace is entered for username', async () => {
    const user = userEvent.setup({ delay: null });
    const loginMock = vi.fn();
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth({ login: loginMock }));
    renderLogin();
    // Type whitespace username + a password so required passes but JS validation catches it
    await user.type(screen.getByLabelText(/username/i), '   ');
    await user.type(screen.getByLabelText(/password/i), 'pw');
    await user.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByText(/please enter both username and password/i)).toBeInTheDocument();
    expect(loginMock).not.toHaveBeenCalled();
  });

  it('calls login with trimmed username and password', async () => {
    const user = userEvent.setup({ delay: null });
    const loginMock = vi.fn().mockResolvedValueOnce({ kind: 'success', user: { id: 'u1', username: 'alice' } });
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth({ login: loginMock }));
    renderLogin();

    await user.type(screen.getByLabelText(/username/i), '  alice  ');
    await user.type(screen.getByLabelText(/password/i), 'secret');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(loginMock).toHaveBeenCalledWith('alice', 'secret');
  });
});

describe('LoginPage — MFA challenge step', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSsoDisabled();
  });

  it('shows MFA form when login returns mfa_required', async () => {
    const user = userEvent.setup({ delay: null });
    const loginMock = vi.fn().mockResolvedValueOnce({
      kind: 'mfa_required',
      mfaChallengeToken: 'tok123',
      expiresIn: 300,
    });
    const verifyMock = vi.fn();
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(
      makeAuth({ login: loginMock, verifyMfaChallenge: verifyMock }),
    );
    renderLogin();

    await user.type(screen.getByLabelText(/username/i), 'alice');
    await user.type(screen.getByLabelText(/password/i), 'pw');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(loginMock).toHaveBeenCalledWith('alice', 'pw');
    expect(await screen.findByLabelText(/authenticator code/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /verify/i })).toBeInTheDocument();
  });

  it('submits TOTP code via verifyMfaChallenge', async () => {
    const user = userEvent.setup({ delay: null });
    const loginMock = vi.fn().mockResolvedValueOnce({
      kind: 'mfa_required',
      mfaChallengeToken: 'tok123',
      expiresIn: 300,
    });
    const verifyMock = vi.fn().mockResolvedValueOnce({ id: 'u1', username: 'alice' });
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(
      makeAuth({ login: loginMock, verifyMfaChallenge: verifyMock }),
    );
    renderLogin();

    await user.type(screen.getByLabelText(/username/i), 'alice');
    await user.type(screen.getByLabelText(/password/i), 'pw');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await user.type(await screen.findByLabelText(/authenticator code/i), '123456');
    await user.click(screen.getByRole('button', { name: /verify/i }));

    expect(verifyMock).toHaveBeenCalledWith('tok123', '123456');
  });

  it('on expired challenge, shows error and returns to credentials step', async () => {
    const user = userEvent.setup({ delay: null });
    const loginMock = vi.fn().mockResolvedValueOnce({
      kind: 'mfa_required',
      mfaChallengeToken: 'tok123',
      expiresIn: 300,
    });
    const verifyMock = vi.fn().mockRejectedValueOnce(new Error('challenge expired'));
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(
      makeAuth({ login: loginMock, verifyMfaChallenge: verifyMock }),
    );
    renderLogin();

    await user.type(screen.getByLabelText(/username/i), 'alice');
    await user.type(screen.getByLabelText(/password/i), 'pw');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await user.type(await screen.findByLabelText(/authenticator code/i), '000000');
    await user.click(screen.getByRole('button', { name: /verify/i }));

    expect(await screen.findByText(/sign-in session expired/i)).toBeInTheDocument();
    // Should be back on credentials step
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
  });
});

describe('LoginPage — SSO button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders SSO button when oidc.enabled is true', () => {
    mockSsoEnabled('Entra ID');
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth());
    renderLogin();
    expect(screen.getByRole('button', { name: /sign in with entra id/i })).toBeInTheDocument();
  });

  it('does not render SSO button when oidc.enabled is false', () => {
    mockSsoDisabled();
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth());
    renderLogin();
    expect(screen.queryByRole('button', { name: /sign in with/i })).not.toBeInTheDocument();
  });

  it('click → mutateAsync() → window.location.href assigned', async () => {
    const user = userEvent.setup({ delay: null });
    const mutateAsync = vi.fn().mockResolvedValueOnce({ authorization_url: 'https://idp.example.com/authorize' });
    (useSsoProviders as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { oidc: { enabled: true, provider_name: 'Entra ID' } },
    });
    (useStartOidcLogin as ReturnType<typeof vi.fn>).mockReturnValue({ mutateAsync, isPending: false });
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth());

    // Stub window.location.href
    const locationDescriptor = Object.getOwnPropertyDescriptor(window, 'location');
    const hrefSetter = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { ...window.location, set href(v: string) { hrefSetter(v); } },
      writable: true,
      configurable: true,
    });

    try {
      renderLogin();
      await user.click(screen.getByRole('button', { name: /sign in with entra id/i }));
      expect(mutateAsync).toHaveBeenCalledOnce();
      await vi.waitFor(() => {
        expect(hrefSetter).toHaveBeenCalledWith('https://idp.example.com/authorize');
      });
    } finally {
      if (locationDescriptor) {
        Object.defineProperty(window, 'location', locationDescriptor);
      }
    }
  });
});
