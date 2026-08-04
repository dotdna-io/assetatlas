// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

import { RenameSessionModal } from '../RenameSessionModal';

describe('RenameSessionModal', () => {
  it('renders nothing when closed', () => {
    render(
      <RenameSessionModal
        isOpen={false}
        initialTitle="Old"
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText('Title')).not.toBeInTheDocument();
  });

  it('seeds the input with the initial title and submits a new value', () => {
    const onSubmit = vi.fn();
    render(
      <RenameSessionModal
        isOpen
        initialTitle="Old"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );
    const input = screen.getByLabelText('Title') as HTMLInputElement;
    expect(input.value).toBe('Old');
    fireEvent.change(input, { target: { value: 'New' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onSubmit).toHaveBeenCalledWith('New');
  });

  it('no-ops (via onCancel) when title unchanged', () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <RenameSessionModal
        isOpen
        initialTitle="Old"
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
  });

  it('submits on Enter key', () => {
    const onSubmit = vi.fn();
    render(
      <RenameSessionModal
        isOpen
        initialTitle="Old"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );
    const input = screen.getByLabelText('Title');
    fireEvent.change(input, { target: { value: 'Changed' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSubmit).toHaveBeenCalledWith('Changed');
  });

  it('calls onCancel when Cancel clicked', () => {
    const onCancel = vi.fn();
    render(
      <RenameSessionModal
        isOpen
        initialTitle="Old"
        onSubmit={vi.fn()}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
  });
});
