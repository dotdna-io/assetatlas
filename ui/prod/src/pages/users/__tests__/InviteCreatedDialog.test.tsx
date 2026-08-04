// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { InviteCreatedDialog } from '../InviteCreatedDialog';

describe('InviteCreatedDialog', () => {
  it('shows the invite URL in a read-only input', () => {
    render(
      <InviteCreatedDialog
        isOpen
        onClose={() => undefined}
        username="alice"
        email="alice@example.com"
        inviteUrl="https://example.test/onboarding?token=abc"
        emailSent={true}
        expiresAt="2026-05-02T10:00:00Z"
      />,
    );
    const input = screen.getByDisplayValue('https://example.test/onboarding?token=abc');
    // jsdom lowercases attribute names; assert the boolean readOnly property.
    expect(input).toHaveAttribute('readonly');
  });

  it('confirms email was sent when emailSent=true', () => {
    render(
      <InviteCreatedDialog
        isOpen
        onClose={() => undefined}
        username="alice"
        email="alice@example.com"
        inviteUrl="https://example.test/onboarding?token=abc"
        emailSent={true}
        expiresAt={null}
      />,
    );
    expect(screen.getByText(/invitation email has been sent/i)).toBeInTheDocument();
  });

  it('warns when the email could not be sent', () => {
    render(
      <InviteCreatedDialog
        isOpen
        onClose={() => undefined}
        username="alice"
        email="alice@example.com"
        inviteUrl="https://example.test/onboarding?token=abc"
        emailSent={false}
        expiresAt={null}
      />,
    );
    expect(screen.getByText(/could not be sent/i)).toBeInTheDocument();
  });

  it('copies the URL to clipboard when the Copy button is clicked', async () => {
    render(
      <InviteCreatedDialog
        isOpen
        onClose={() => undefined}
        username="alice"
        email="alice@example.com"
        inviteUrl="https://example.test/onboarding?token=abc"
        emailSent={true}
        expiresAt={null}
      />,
    );
    // Adjustment: userEvent.setup() installs its own clipboard stub on the
    // window, so spy on writeText AFTER setup to capture the actual call.
    const user = userEvent.setup();
    const writeSpy = vi.spyOn(navigator.clipboard, 'writeText');
    await user.click(screen.getByRole('button', { name: /copy link/i }));
    expect(writeSpy).toHaveBeenCalledWith(
      'https://example.test/onboarding?token=abc',
    );
    // Adjustment: the success state button reads "Copied!" (with exclamation).
    expect(await screen.findByRole('button', { name: /copied/i })).toBeInTheDocument();
  });
});
