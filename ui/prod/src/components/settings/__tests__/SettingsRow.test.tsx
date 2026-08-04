// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SettingsRow } from '../SettingsRow';

describe('SettingsRow', () => {
  it('renders name, description, meta, and control', () => {
    render(
      <SettingsRow
        name="Asset retention"
        description="How many days of asset history to keep"
        meta="Default: 90"
        control={<input data-testid="ctl" />}
      />,
    );
    expect(screen.getByText('Asset retention')).toBeInTheDocument();
    expect(screen.getByText('How many days of asset history to keep')).toBeInTheDocument();
    expect(screen.getByText('Default: 90')).toBeInTheDocument();
    expect(screen.getByTestId('ctl')).toBeInTheDocument();
  });

  it('calls onReset when reset clicked', () => {
    const onReset = vi.fn();
    render(
      <SettingsRow
        name="X"
        control={<span />}
        onReset={onReset}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /reset/i }));
    expect(onReset).toHaveBeenCalledOnce();
  });

  it('does not render reset button when onReset not provided', () => {
    render(<SettingsRow name="X" control={<span />} />);
    expect(screen.queryByRole('button', { name: /reset/i })).toBeNull();
  });

  it('uses resetLabel as accessible name when provided', () => {
    const onReset = vi.fn();
    render(
      <SettingsRow
        name="X"
        control={<span />}
        onReset={onReset}
        resetLabel="Reset X to default"
      />,
    );
    expect(screen.getByRole('button', { name: 'Reset X to default' })).toBeInTheDocument();
  });
});
