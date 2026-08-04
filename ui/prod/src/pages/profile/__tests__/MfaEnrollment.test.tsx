// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MfaEnrollment } from '../MfaEnrollment';

// Mock the entire auth API module
vi.mock('@/api/auth', () => ({
  useMfaStatus: vi.fn(),
  useSetupMfa: vi.fn(),
  useVerifyMfa: vi.fn(),
  useDisableMfa: vi.fn(),
}));

// Mock useAuth so we can control the current user
vi.mock('@/auth', () => ({
  useAuth: vi.fn(),
}));

import {
  useMfaStatus,
  useSetupMfa,
  useVerifyMfa,
  useDisableMfa,
} from '@/api/auth';
import { useAuth } from '@/auth';

// Typed helpers
const mockUseMfaStatus = useMfaStatus as ReturnType<typeof vi.fn>;
const mockUseSetupMfa = useSetupMfa as ReturnType<typeof vi.fn>;
const mockUseVerifyMfa = useVerifyMfa as ReturnType<typeof vi.fn>;
const mockUseDisableMfa = useDisableMfa as ReturnType<typeof vi.fn>;
const mockUseAuth = useAuth as ReturnType<typeof vi.fn>;

function makeIdleMutation(mutateAsync = vi.fn()) {
  return { mutateAsync, isPending: false };
}

function setDefaults() {
  mockUseAuth.mockReturnValue({ user: { is_ldap_user: false, is_oidc_user: false } });
  mockUseMfaStatus.mockReturnValue({ data: { enabled: false }, isLoading: false });
  mockUseSetupMfa.mockReturnValue(makeIdleMutation());
  mockUseVerifyMfa.mockReturnValue(makeIdleMutation());
  mockUseDisableMfa.mockReturnValue(makeIdleMutation());
}

describe('MfaEnrollment', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setDefaults();
  });

  it('shows enable button when MFA is disabled', () => {
    render(<MfaEnrollment />);
    expect(screen.getByRole('button', { name: /enable two-factor authentication/i })).toBeInTheDocument();
    expect(screen.queryByText(/enabled/i)).not.toBeInTheDocument();
  });

  it('shows QR code and secret after setup mutation succeeds', async () => {
    const user = userEvent.setup({ delay: null });
    const mutateAsync = vi.fn().mockResolvedValue({
      secret: 'TESTSECRET1234',
      provisioning_uri: 'otpauth://totp/AssetAtlas:alice?secret=TESTSECRET1234&issuer=AssetAtlas',
    });
    mockUseSetupMfa.mockReturnValue({ mutateAsync, isPending: false });

    render(<MfaEnrollment />);

    await user.click(screen.getByRole('button', { name: /enable two-factor authentication/i }));

    await waitFor(() => {
      expect(screen.getByText('TESTSECRET1234')).toBeInTheDocument();
    });
    // QR svg rendered by qrcode.react
    expect(screen.getByLabelText(/scan with your authenticator app/i)).toBeInTheDocument();
  });

  it('returns to disabled state after verify succeeds', async () => {
    const user = userEvent.setup({ delay: null });

    // Pre-seed: component starts in enrollment view (setupData already set)
    const setupMutateAsync = vi.fn().mockResolvedValue({
      secret: 'ABCDEFGH1234',
      provisioning_uri: 'otpauth://totp/test?secret=ABCDEFGH1234',
    });
    mockUseSetupMfa.mockReturnValue({ mutateAsync: setupMutateAsync, isPending: false });

    const verifyMutateAsync = vi.fn().mockResolvedValue({ enabled: true });
    mockUseVerifyMfa.mockReturnValue({ mutateAsync: verifyMutateAsync, isPending: false });

    render(<MfaEnrollment />);

    // Click Enable to enter enrollment view
    await user.click(screen.getByRole('button', { name: /enable two-factor authentication/i }));
    await waitFor(() => expect(screen.getByLabelText(/6-digit code/i)).toBeInTheDocument());

    // Fill in the code and submit
    await user.type(screen.getByLabelText(/6-digit code/i), '123456');
    await user.click(screen.getByRole('button', { name: /verify and enable/i }));

    // Enrollment form should disappear (setupData cleared)
    await waitFor(() => {
      expect(screen.queryByLabelText(/6-digit code/i)).not.toBeInTheDocument();
    });
  });

  it('shows status and disable button when MFA is enabled', () => {
    mockUseMfaStatus.mockReturnValue({
      data: { enabled: true },
      isLoading: false,
    });

    render(<MfaEnrollment />);

    expect(screen.getByText(/enabled/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /disable two-factor authentication/i })).toBeInTheDocument();
  });

  it('opens disable dialog when Disable button is clicked', async () => {
    const user = userEvent.setup({ delay: null });
    mockUseMfaStatus.mockReturnValue({
      data: { enabled: true },
      isLoading: false,
    });

    render(<MfaEnrollment />);
    await user.click(screen.getByRole('button', { name: /disable two-factor authentication/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/6-digit code/i)).toBeInTheDocument();
  });

  it('shows SSO notice and hides enable button for LDAP users', () => {
    mockUseAuth.mockReturnValue({ user: { is_ldap_user: true, is_oidc_user: false } });

    render(<MfaEnrollment />);

    expect(screen.queryByRole('button', { name: /enable two-factor authentication/i })).not.toBeInTheDocument();
    expect(screen.getByText(/not yet available for accounts authenticated via SSO\/LDAP/i)).toBeInTheDocument();
  });

  it('shows SSO notice and hides enable button for OIDC users', () => {
    mockUseAuth.mockReturnValue({ user: { is_ldap_user: false, is_oidc_user: true } });

    render(<MfaEnrollment />);

    expect(screen.queryByRole('button', { name: /enable two-factor authentication/i })).not.toBeInTheDocument();
    expect(screen.getByText(/not yet available for accounts authenticated via SSO\/LDAP/i)).toBeInTheDocument();
  });
});
