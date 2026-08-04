// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Adjustment: OnboardingPage exports as `default`, so import accordingly.
import OnboardingPage from '../OnboardingPage';

vi.mock('@/api/auth', () => ({
  useValidateOnboardingToken: vi.fn(),
  useCompleteOnboarding: vi.fn(),
}));
vi.mock('@/components/ui/InlineStatus', () => ({
  InlineStatus: () => null,
  useInlineStatus: () => [null, vi.fn()],
}));
// Adjustment: stub auth + theme + title hooks that the page uses but are
// orthogonal to the behaviour under test.
vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({ isAuthenticated: false }),
}));
vi.mock('@/hooks', () => ({
  useDarkMode: () => ({ isDarkMode: false, toggle: vi.fn() }),
  useDocumentTitle: () => undefined,
}));

import { useValidateOnboardingToken, useCompleteOnboarding } from '@/api/auth';

function renderWith(searchString: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/onboarding${searchString}`]}>
        <Routes>
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route path="/login" element={<div>LOGIN PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('OnboardingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (useCompleteOnboarding as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });
  });

  it('shows missing-token state when there is no token in the URL', () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
    });
    renderWith('');
    expect(screen.getByText(/invitation link is missing/i)).toBeInTheDocument();
  });

  it('shows loading state while validating', () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: true,
    });
    renderWith('?token=abc');
    expect(screen.getByText(/checking your invitation/i)).toBeInTheDocument();
  });

  it('shows invalid-link state when validate returns valid=false', () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { valid: false },
      isLoading: false,
    });
    renderWith('?token=expired');
    expect(screen.getByText(/no longer valid/i)).toBeInTheDocument();
  });

  it('greets the user by name when token is valid', () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { valid: true, username: 'alice', email: 'alice@example.com', requires_mfa: false },
      isLoading: false,
    });
    renderWith('?token=valid');
    expect(screen.getByText(/welcome.*alice/i)).toBeInTheDocument();
  });

  it('rejects mismatched passwords', async () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { valid: true, username: 'alice', email: 'a@e.com', requires_mfa: false },
      isLoading: false,
    });
    const mutate = vi.fn();
    (useCompleteOnboarding as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate,
      isPending: false,
    });
    renderWith('?token=valid');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/^new password/i), 'StrongPassword1!');
    await user.type(screen.getByLabelText(/confirm password/i), 'Different1!');
    // Adjustment: button text is "Activate Account".
    await user.click(screen.getByRole('button', { name: /activate account/i }));

    // Adjustment: the page renders both a top-level error banner AND a
    // per-field error span with the same text, so use findAllByText.
    const matches = await screen.findAllByText(/do not match/i);
    expect(matches.length).toBeGreaterThan(0);
    expect(mutate).not.toHaveBeenCalled();
  });

  it('submits when form is valid', async () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { valid: true, username: 'alice', email: 'a@e.com', requires_mfa: false },
      isLoading: false,
    });
    const mutate = vi.fn();
    (useCompleteOnboarding as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate,
      isPending: false,
    });
    renderWith('?token=valid');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/^new password/i), 'StrongPassword1!');
    await user.type(screen.getByLabelText(/confirm password/i), 'StrongPassword1!');
    await user.click(screen.getByRole('button', { name: /activate account/i }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      token: 'valid',
      new_password: 'StrongPassword1!',
    });
  });

  it('shows MFA notice on success when requires_mfa: true', async () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { valid: true, username: 'bob', email: 'bob@example.com', requires_mfa: true },
      isLoading: false,
    });
    let capturedOnSuccess: ((resp: { username: string }) => void) | undefined;
    (useCompleteOnboarding as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (opts: { onSuccess: (resp: { username: string }) => void }) => {
        capturedOnSuccess = opts.onSuccess;
        return { mutate: vi.fn(), isPending: false };
      },
    );
    renderWith('?token=valid');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/^new password/i), 'StrongPassword1!');
    await user.type(screen.getByLabelText(/confirm password/i), 'StrongPassword1!');
    await user.click(screen.getByRole('button', { name: /activate account/i }));

    // Simulate the onSuccess callback firing
    capturedOnSuccess!({ username: 'bob' });

    await screen.findByText(/account activated/i);
    expect(screen.getByText(/two-factor authentication required/i)).toBeInTheDocument();
    expect(screen.getByText(/after signing in, set up your authenticator app under profile/i)).toBeInTheDocument();
  });

  it('does not show MFA notice when requires_mfa: false', async () => {
    (useValidateOnboardingToken as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { valid: true, username: 'carol', email: 'carol@example.com', requires_mfa: false },
      isLoading: false,
    });
    let capturedOnSuccess: ((resp: { username: string }) => void) | undefined;
    (useCompleteOnboarding as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (opts: { onSuccess: (resp: { username: string }) => void }) => {
        capturedOnSuccess = opts.onSuccess;
        return { mutate: vi.fn(), isPending: false };
      },
    );
    renderWith('?token=valid');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/^new password/i), 'StrongPassword1!');
    await user.type(screen.getByLabelText(/confirm password/i), 'StrongPassword1!');
    await user.click(screen.getByRole('button', { name: /activate account/i }));

    capturedOnSuccess!({ username: 'carol' });

    await screen.findByText(/account activated/i);
    expect(screen.queryByText(/two-factor authentication required/i)).not.toBeInTheDocument();
  });
});
