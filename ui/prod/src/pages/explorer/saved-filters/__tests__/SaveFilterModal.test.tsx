// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SaveFilterModal } from '../SaveFilterModal';

describe('SaveFilterModal', () => {
  it('renders nothing when closed', () => {
    render(<SaveFilterModal isOpen={false} mode="create" onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
  });

  it('submits a private filter by default for non-admins (no access choice)', () => {
    const onSubmit = vi.fn();
    render(<SaveFilterModal isOpen mode="create" onSubmit={onSubmit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Prod' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onSubmit).toHaveBeenCalledWith({ name: 'Prod', description: '', visibility: 'private' });
  });

  it('disables Save on empty name', () => {
    render(<SaveFilterModal isOpen mode="create" onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
  });

  it('seeds inputs in edit mode', () => {
    render(
      <SaveFilterModal isOpen mode="edit" initialName="Old" initialDescription="d"
        onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Old');
  });

  it('does not submit on Enter while busy (guards double-submit)', () => {
    const onSubmit = vi.fn();
    render(
      <SaveFilterModal isOpen mode="create" busy initialName="Servers"
        onSubmit={onSubmit} onCancel={vi.fn()} />,
    );
    fireEvent.keyDown(screen.getByLabelText('Name'), { key: 'Enter' });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('no longer offers an "anyone with the link" radio (sharing is a row action)', () => {
    render(<SaveFilterModal isOpen mode="create" canPublish onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByLabelText(/anyone with the link/i)).not.toBeInTheDocument();
  });

  it('hides the General access section entirely for non-admins', () => {
    render(<SaveFilterModal isOpen mode="create" onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByText(/general access/i)).not.toBeInTheDocument();
  });

  it('shows the Published option to admins', () => {
    render(<SaveFilterModal isOpen mode="create" canPublish onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText(/published to the library/i)).toBeInTheDocument();
  });

  it('hides Published from non-admins', () => {
    render(<SaveFilterModal isOpen mode="create" onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByLabelText(/published to the library/i)).not.toBeInTheDocument();
  });

  it('seeds the library (org) selection in edit mode for admins', () => {
    render(<SaveFilterModal isOpen mode="edit" canPublish initialName="X"
      initialVisibility="org" onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect((screen.getByLabelText(/published to the library/i) as HTMLInputElement).checked).toBe(true);
  });

  it('submits org visibility when Published is chosen', () => {
    const onSubmit = vi.fn();
    render(<SaveFilterModal isOpen mode="create" canPublish onSubmit={onSubmit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Fleet' } });
    fireEvent.click(screen.getByLabelText(/published to the library/i));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onSubmit).toHaveBeenCalledWith({ name: 'Fleet', description: '', visibility: 'org' });
  });
});
