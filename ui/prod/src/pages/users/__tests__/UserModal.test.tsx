// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { UserModal } from '../UserModal';

const noop = () => undefined;

describe('UserModal — invite_only toggle', () => {
  it('hides the password field by default in create mode (invite_only=true)', () => {
    render(
      <UserModal
        isOpen
        onClose={noop}
        mode="create"
        onSubmit={noop}
        isSubmitting={false}
      />,
    );
    // Adjustment: ToggleSwitch renders a <button role="switch"> with the
    // label in a sibling <span> (no htmlFor wiring), so we locate it by
    // role and assert aria-checked rather than getByLabelText/.toBeChecked.
    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText(/send onboarding invite/i)).toBeInTheDocument();
    // No password input — labels lack htmlFor linkage so query by the
    // visible "Password" label text instead.
    expect(screen.queryByText(/^password$/i)).not.toBeInTheDocument();
  });

  it('shows the password field when the toggle is off', async () => {
    render(
      <UserModal
        isOpen
        onClose={noop}
        mode="create"
        onSubmit={noop}
        isSubmitting={false}
      />,
    );
    // Adjustment: delay:null avoids a stale requestAnimationFrame focus call
    // (from a previous test's Modal cleanup) stealing focus mid-interaction.
    const user = userEvent.setup({ delay: null });
    await user.click(screen.getByRole('switch'));
    expect(screen.getByText(/^password$/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/minimum 8 characters/i)).toBeInTheDocument();
  });

  it('submits with invite_only=true and no password when toggle is on', async () => {
    const onSubmit = vi.fn();
    render(
      <UserModal
        isOpen
        onClose={noop}
        mode="create"
        onSubmit={onSubmit}
        isSubmitting={false}
      />,
    );
    const user = userEvent.setup({ delay: null });
    // Adjustment: the form labels are not associated with their inputs
    // (no htmlFor/id), so we target by placeholder text instead.
    await user.type(screen.getByPlaceholderText(/jsmith$/i), 'newuser');
    await user.type(screen.getByPlaceholderText(/jsmith@company\.com/i), 'newuser@example.com');
    // Adjustment: in create mode the submit button reads "Create User".
    await user.click(screen.getByRole('button', { name: /create user/i }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        username: 'newuser',
        email: 'newuser@example.com',
        invite_only: true,
      }),
    );
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('password');
  });
});
