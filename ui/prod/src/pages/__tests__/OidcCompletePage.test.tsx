// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import OidcCompletePage from '../OidcCompletePage';

vi.mock('@/auth/useAuth', () => ({
  useAuth: vi.fn(),
}));
vi.mock('@/hooks', () => ({
  useDocumentTitle: () => undefined,
}));

import { useAuth } from '@/auth/useAuth';

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

function renderPage(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/oidc-complete${search}`]}>
      <Routes>
        <Route path="/oidc-complete" element={<OidcCompletePage />} />
        <Route path="/login" element={<div>LOGIN</div>} />
        <Route path="/dashboard" element={<div>DASHBOARD</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('OidcCompletePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders friendly error message when ?error=oidc_failed', () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth());
    renderPage('?error=oidc_failed');
    expect(screen.getByText('Single sign-on failed. Please try again.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to sign-in/i })).toBeInTheDocument();
  });

  it('renders "Missing sign-in code." + back-to-sign-in link when no code and no error', async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth());
    renderPage('');
    await waitFor(() => {
      expect(screen.getByText('Missing sign-in code.')).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: /back to sign-in/i })).toBeInTheDocument();
  });

  it('calls exchangeOidcCode(code) exactly once when ?code=xyz present', async () => {
    const exchangeOidcCode = vi.fn().mockResolvedValue({ id: 'u1', username: 'alice' });
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth({ exchangeOidcCode }));
    renderPage('?code=xyz');
    await waitFor(() => {
      expect(exchangeOidcCode).toHaveBeenCalledOnce();
      expect(exchangeOidcCode).toHaveBeenCalledWith('xyz');
    });
  });

  it('navigates to /dashboard on successful exchange', async () => {
    const exchangeOidcCode = vi.fn().mockResolvedValue({ id: 'u1', username: 'alice' });
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth({ exchangeOidcCode }));
    renderPage('?code=xyz');
    await waitFor(() => {
      expect(screen.getByText('DASHBOARD')).toBeInTheDocument();
    });
  });

  it('renders error when exchangeOidcCode rejects with generic error', async () => {
    const exchangeOidcCode = vi.fn().mockRejectedValue(new Error('OIDC sign-in failed'));
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth({ exchangeOidcCode }));
    renderPage('?code=xyz');
    await waitFor(() => {
      expect(screen.getByText('OIDC sign-in failed')).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: /back to sign-in/i })).toBeInTheDocument();
  });

  it('renders "session expired" when error code is invalid_or_expired_code', async () => {
    const err: Error & { code?: string } = new Error('SSO exchange code is invalid or has already been used.');
    err.code = 'invalid_or_expired_code';
    const exchangeOidcCode = vi.fn().mockRejectedValue(err);
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth({ exchangeOidcCode }));
    renderPage('?code=xyz');
    await waitFor(() => {
      expect(screen.getByText('Sign-in session expired. Please start over.')).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: /back to sign-in/i })).toBeInTheDocument();
  });

  it('does NOT call exchangeOidcCode a second time on re-render', async () => {
    const exchangeOidcCode = vi.fn().mockResolvedValue({ id: 'u1', username: 'alice' });
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue(makeAuth({ exchangeOidcCode }));
    const { rerender } = render(
      <MemoryRouter initialEntries={['/oidc-complete?code=xyz']}>
        <Routes>
          <Route path="/oidc-complete" element={<OidcCompletePage />} />
          <Route path="/dashboard" element={<div>DASHBOARD</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(exchangeOidcCode).toHaveBeenCalledOnce());
    // Force a re-render — the ref guard should prevent a second call.
    rerender(
      <MemoryRouter initialEntries={['/oidc-complete?code=xyz']}>
        <Routes>
          <Route path="/oidc-complete" element={<OidcCompletePage />} />
          <Route path="/dashboard" element={<div>DASHBOARD</div>} />
        </Routes>
      </MemoryRouter>,
    );
    // Still only called once.
    expect(exchangeOidcCode).toHaveBeenCalledOnce();
  });
});
