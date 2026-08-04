// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

import { EmailSettingsTab } from '../EmailSettingsTab';
import { apiClient } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

const BASE_SETTINGS = {
  enabled: true,
  smtp_host: 'smtp.example.com',
  smtp_port: 587,
  smtp_security: 'starttls' as const,
  smtp_username: 'user@example.com',
  smtp_password: '__SET__' as const,
  from_address: 'no-reply@example.com',
  from_display_name: 'AssetAtlas',
  reply_to_address: '',
};

function renderTab() {
  return render(<EmailSettingsTab />);
}

describe('EmailSettingsTab — initial render', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue(BASE_SETTINGS);
  });

  it('loads and populates form fields on mount', async () => {
    renderTab();
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith('/settings/email'));
    expect(await screen.findByDisplayValue('smtp.example.com')).toBeInTheDocument();
    expect(screen.getByDisplayValue('587')).toBeInTheDocument();
    expect(screen.getByDisplayValue('user@example.com')).toBeInTheDocument();
    expect(screen.getByDisplayValue('no-reply@example.com')).toBeInTheDocument();
    expect(screen.getByDisplayValue('AssetAtlas')).toBeInTheDocument();
  });

  it('renders an empty password field with a "(saved)" placeholder when a password is stored', async () => {
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');
    const pwd = screen.getByLabelText(/password/i) as HTMLInputElement;
    expect(pwd.value).toBe('');
    expect(pwd.placeholder).toMatch(/saved/i);
  });

  it('does not show "(saved)" when no password is stored', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...BASE_SETTINGS, enabled: false, smtp_password: '',
    });
    renderTab();
    await waitFor(() => expect(apiClient.get).toHaveBeenCalled());
    const pwd = screen.getByLabelText(/password/i) as HTMLInputElement;
    expect(pwd.placeholder).not.toMatch(/saved/i);
  });
});

describe('EmailSettingsTab — save', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue(BASE_SETTINGS);
    (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({});
  });

  it('fires PUT only for fields the user changed', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');

    await user.clear(screen.getByLabelText(/port/i));
    await user.type(screen.getByLabelText(/port/i), '465');
    await user.clear(screen.getByLabelText(/display name/i));
    await user.type(screen.getByLabelText(/display name/i), 'CustomName');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(apiClient.put).toHaveBeenCalled();
    });
    const urls = (apiClient.put as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(urls.sort()).toEqual([
      '/settings/email/from_display_name',
      '/settings/email/smtp_port',
    ]);
  });

  it('does NOT PUT smtp_password when the field was not touched', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');

    await user.clear(screen.getByLabelText(/smtp host/i));
    await user.type(screen.getByLabelText(/smtp host/i), 'new.example.com');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    const urls = (apiClient.put as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(urls).not.toContain('/settings/email/smtp_password');
  });

  it('PUTs smtp_password when the user types a new value', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');
    await user.type(screen.getByLabelText(/password/i), 'newpass');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    const pwCall = (apiClient.put as ReturnType<typeof vi.fn>).mock.calls.find(
      (c) => c[0] === '/settings/email/smtp_password',
    );
    expect(pwCall).toBeDefined();
    expect(pwCall![1]).toEqual({ value: 'newpass' });
  });

  it('PUTs empty smtp_password when user clears a previously-saved password', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');
    // Type something then clear: mode transitions new -> clear.
    await user.type(screen.getByLabelText(/password/i), 'x');
    await user.clear(screen.getByLabelText(/password/i));
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    const pwCall = (apiClient.put as ReturnType<typeof vi.fn>).mock.calls.find(
      (c) => c[0] === '/settings/email/smtp_password',
    );
    expect(pwCall).toBeDefined();
    expect(pwCall![1]).toEqual({ value: '' });
  });
});

class FakeApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

describe('EmailSettingsTab — test email', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue(BASE_SETTINGS);
  });

  it('opens a modal with a recipient input when Send Test is clicked', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');
    await user.click(screen.getByRole('button', { name: /send test/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/recipient/i)).toBeInTheDocument();
  });

  it('sends and shows success', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const user = userEvent.setup();
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');
    await user.click(screen.getByRole('button', { name: /send test/i }));
    const input = await screen.findByLabelText(/recipient/i);
    await user.clear(input);
    await user.type(input, 'admin@example.com');
    await user.click(screen.getByRole('button', { name: /^send$/i }));

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/settings/email/test',
        { recipient: 'admin@example.com' },
      );
    });
    expect(await screen.findByText(/sent|success/i)).toBeInTheDocument();
  });

  it('shows the SMTP error string when apiClient throws ApiError (400)', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockRejectedValue(
      new FakeApiError('400', 400, { success: false, error: '550 relay rejected' }),
    );
    const user = userEvent.setup();
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');
    await user.click(screen.getByRole('button', { name: /send test/i }));
    await user.type(await screen.findByLabelText(/recipient/i), 'admin@example.com');
    await user.click(screen.getByRole('button', { name: /^send$/i }));
    expect(await screen.findByText(/550 relay rejected/i)).toBeInTheDocument();
  });

  it('keeps the Send Test button enabled when outbound email is disabled', async () => {
    // You verify the relay first, then flip the master switch — so the toggle
    // must not gate the test. Only host + from address do.
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...BASE_SETTINGS, enabled: false,
    });
    renderTab();
    await screen.findByDisplayValue('smtp.example.com');
    const btn = screen.getByRole('button', { name: /send test/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it('disables the Send Test button when the saved config has no SMTP host', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...BASE_SETTINGS, smtp_host: '',
    });
    renderTab();
    await screen.findByDisplayValue('no-reply@example.com');
    const btn = screen.getByRole('button', { name: /send test/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
