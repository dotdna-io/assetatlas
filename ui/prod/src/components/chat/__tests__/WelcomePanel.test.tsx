// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

import { WelcomePanel } from '../WelcomePanel';

describe('WelcomePanel', () => {
  it('renders 3 starter prompts', () => {
    render(<WelcomePanel onPrompt={vi.fn()} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);
  });

  it('invokes onPrompt with the full prompt text', () => {
    const onPrompt = vi.fn();
    render(<WelcomePanel onPrompt={onPrompt} />);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[0]);
    expect(onPrompt).toHaveBeenCalledTimes(1);
    expect(onPrompt.mock.calls[0][0]).toMatch(/assets/i);
  });

  it('disables prompt buttons when disabled prop is set', () => {
    render(<WelcomePanel onPrompt={vi.fn()} disabled />);
    for (const b of screen.getAllByRole('button')) {
      expect(b).toBeDisabled();
    }
  });
});
