// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { EmailChannelForm } from '../EmailChannelForm';
import type { AlertChannel } from '@/api/channels';
import type { EmailSettings } from '@/api/settings';

// `vi.mock` is hoisted and applies for the whole file, so a single static
// mock can't return different data per test the way the brief's
// vi.doMock + vi.resetModules + dynamic-import sketch intended — that
// approach also risks re-evaluating '@tanstack/react-query' as a second
// module instance, which would desync the dynamically re-imported form's
// useQuery from the QueryClientProvider created before the reset. Instead,
// the mock reads a module-scoped variable that each test/beforeEach sets,
// giving per-test control over useApiQuery's return without touching the
// module cache.
let mockEmailData: Partial<EmailSettings> | undefined;

vi.mock('@/api/hooks', () => ({
  useApiQuery: () => ({ data: mockEmailData }),
}));

function renderForm(props: Parameters<typeof EmailChannelForm>[0]) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <EmailChannelForm {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockEmailData = { enabled: true, smtp_host: 'smtp.x.com' } as EmailSettings;
});

describe('EmailChannelForm', () => {
  it('seeds recipients from channel.config', () => {
    renderForm({
      channel: {
        id: 'c1', type: 'email', name: 'ops', enabled: true,
        config: { recipients: ['a@x.com', 'b@y.com'] },
      } as AlertChannel,
      onConfigChange: vi.fn(),
    });
    expect(screen.getByDisplayValue('a@x.com')).toBeInTheDocument();
    expect(screen.getByDisplayValue('b@y.com')).toBeInTheDocument();
  });

  it('emits { recipients } when a recipient is edited', () => {
    const onConfigChange = vi.fn();
    renderForm({ onConfigChange });
    fireEvent.click(screen.getByRole('button', { name: /add recipient/i }));
    // Anchored: an unanchored /recipient 1/i also matches the "Remove
    // recipient 1" button's aria-label, so getByLabelText throws on
    // multiple matches without the exact-match anchors.
    const input = screen.getByLabelText(/^recipient 1$/i);
    fireEvent.change(input, { target: { value: 'new@x.com' } });
    expect(onConfigChange).toHaveBeenLastCalledWith({ recipients: ['new@x.com'] });
  });

  it('does not emit on mount (parent seeds config)', () => {
    const onConfigChange = vi.fn();
    renderForm({ onConfigChange });
    expect(onConfigChange).not.toHaveBeenCalled();
  });
});

describe('EmailChannelForm SMTP hint', () => {
  it('shows the hint when global email is disabled', () => {
    mockEmailData = { enabled: false, smtp_host: '' } as EmailSettings;
    renderForm({ onConfigChange: vi.fn() });
    expect(screen.getByText(/SMTP is not configured/i)).toBeInTheDocument();
  });

  it('does not show the hint when SMTP is configured', () => {
    renderForm({ onConfigChange: vi.fn() });
    expect(screen.queryByText(/SMTP is not configured/i)).not.toBeInTheDocument();
  });
});
