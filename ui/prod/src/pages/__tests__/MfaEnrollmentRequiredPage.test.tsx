// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import MfaEnrollmentRequiredPage from '../MfaEnrollmentRequiredPage';

// Stub useAuth so we can drive isAuthenticated and the mfa enroll calls.
vi.mock('@/auth/useAuth', () => ({
  useAuth: vi.fn(),
}));
vi.mock('@/hooks', () => ({
  useDocumentTitle: () => undefined,
}));

import { useAuth } from '@/auth/useAuth';

function renderWith(searchString: string) {
  return render(
    <MemoryRouter initialEntries={[`/mfa-enrollment-required${searchString}`]}>
      <Routes>
        <Route
          path="/mfa-enrollment-required"
          element={<MfaEnrollmentRequiredPage />}
        />
        <Route path="/dashboard" element={<div>DASHBOARD</div>} />
        <Route path="/login" element={<div>LOGIN PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('MfaEnrollmentRequiredPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('navigates to /dashboard immediately after verify succeeds', async () => {
    const user = userEvent.setup({ delay: null });

    const mfaEnrollSetup = vi.fn().mockResolvedValue({
      secret: 'TESTSECRET1234',
      provisioning_uri:
        'otpauth://totp/AssetAtlas:alice?secret=TESTSECRET1234&issuer=AssetAtlas',
    });

    const mfaEnrollVerify = vi.fn().mockResolvedValue({
      user: { id: 'u1', username: 'alice' },
    });

    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      isAuthenticated: false,
      mfaEnrollSetup,
      mfaEnrollVerify,
    });

    renderWith('?token=enroll-tok-123');

    // Setup call resolves → secret + 6-digit form appear
    await waitFor(() =>
      expect(screen.getByText('TESTSECRET1234')).toBeInTheDocument(),
    );

    // Submit the verify form
    await user.type(screen.getByLabelText(/6-digit code/i), '123456');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    // Should redirect straight to /dashboard
    expect(await screen.findByText('DASHBOARD')).toBeInTheDocument();

    expect(mfaEnrollVerify).toHaveBeenCalledWith('enroll-tok-123', '123456');
  });

  it('shows error and stays on page when verify fails', async () => {
    const user = userEvent.setup({ delay: null });

    const mfaEnrollSetup = vi.fn().mockResolvedValue({
      secret: 'SECRET',
      provisioning_uri: 'otpauth://totp/test?secret=SECRET',
    });
    const mfaEnrollVerify = vi
      .fn()
      .mockRejectedValue(new Error('Invalid code'));

    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      isAuthenticated: false,
      mfaEnrollSetup,
      mfaEnrollVerify,
    });

    renderWith('?token=tok');

    await waitFor(() => expect(screen.getByText('SECRET')).toBeInTheDocument());
    await user.type(screen.getByLabelText(/6-digit code/i), '000000');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    expect(await screen.findByText(/invalid code/i)).toBeInTheDocument();
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument();
  });
});
