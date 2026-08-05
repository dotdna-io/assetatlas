// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AlertChannelsTab } from '../AlertChannelsTab';
import { AlertDeliveryGroupPage } from '../AlertDeliveryGroupPage';
import { apiClient } from '../../../api/client';
import type { AlertChannel, ChannelTypeInfo } from '../../../api/channels';

// Mock the transport only — the real react-query hooks + channelsApi from
// api/channels.ts run unmocked, matching how ReferenceDataTab/DataGroupPage
// test the real hooks against a stubbed apiClient. getApiErrorMessage is a
// plain function (not React-dependent) so importOriginal keeps the real
// implementation while apiClient itself is replaced with mocks.
vi.mock('../../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/client')>();
  return {
    ...actual,
    apiClient: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      del: vi.fn(),
    },
  };
});

const CHANNELS: AlertChannel[] = [
  {
    id: 'c1',
    type: 'webhook',
    name: 'Ops Webhook',
    enabled: true,
    config: { url: 'https://hooks.example.com/a', timeout: 30 },
  },
];

const TYPES: ChannelTypeInfo[] = [
  { type: 'webhook', label: 'Webhook' },
  { type: 'email', label: 'Email' },
  { type: 'syslog', label: 'Syslog' },
];

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

// The "Add channel" button now lives on the sub-tab row (rendered by the group
// page) and reaches it through Outlet context, so the tab is exercised inside
// its real routed layout rather than standalone.
function renderTab() {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/settings/alert-delivery/channels']}>
        <Routes>
          <Route path="/settings/alert-delivery" element={<AlertDeliveryGroupPage />}>
            <Route path="channels" element={<AlertChannelsTab />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function setupApi() {
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url === '/alerts/channels') return Promise.resolve(CHANNELS);
    if (url === '/alerts/channels/types') return Promise.resolve(TYPES);
    // EmailChannelForm queries this to render its SMTP-unconfigured hint;
    // stub it so mounting the form doesn't hit the "unexpected GET" fallback.
    if (url === '/settings/email') return Promise.resolve({ enabled: true, smtp_host: 'smtp.example.com' });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

describe('AlertChannelsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupApi();
  });

  it('renders channel names from the list', async () => {
    renderTab();
    expect(await screen.findByText('Ops Webhook')).toBeInTheDocument();
  });

  it('clicking Test calls channelsApi.test and shows a success row', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      response_code: 200,
      error: null,
    });
    const user = userEvent.setup({ delay: null });
    renderTab();

    await screen.findByText('Ops Webhook');
    await user.click(screen.getByRole('button', { name: /^test$/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/alerts/channels/c1/test'),
    );
    expect(await screen.findByText(/200/)).toBeInTheDocument();
  });

  it('Add channel (webhook) saves with create payload {type, name, config}', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'c2',
      type: 'webhook',
      name: 'New Channel',
      enabled: true,
      config: { url: '', timeout: 30 },
    });
    const user = userEvent.setup({ delay: null });
    renderTab();

    await screen.findByText('Ops Webhook');
    await user.click(screen.getByRole('button', { name: /add channel/i }));

    const nameInput = await screen.findByLabelText(/^name$/i);
    // The modal focuses itself on open (rAF in Modal.tsx); userEvent's
    // delay:null keeps that grab from interleaving mid-typing and swallowing
    // keystrokes. The click is just what a real user would do first.
    await user.click(nameInput);
    await user.type(nameInput, 'New Channel');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/alerts/channels', {
        type: 'webhook',
        name: 'New Channel',
        config: { url: '', timeout: 30 },
      }),
    );
  });

  it('seeds { recipients: [] } and renders EmailChannelForm when Email type is picked', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'c3',
      type: 'email',
      name: 'Escalation Email',
      enabled: true,
      config: { recipients: [] },
    });
    const user = userEvent.setup({ delay: null });
    renderTab();

    await screen.findByText('Ops Webhook');
    await user.click(screen.getByRole('button', { name: /add channel/i }));

    const typeSelect = await screen.findByLabelText(/^type$/i);
    await user.selectOptions(typeSelect, 'email');

    expect(screen.getByText(/email configuration/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add recipient/i })).toBeInTheDocument();

    const nameInput = screen.getByLabelText(/^name$/i);
    await user.click(nameInput);
    await user.type(nameInput, 'Escalation Email');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/alerts/channels', {
        type: 'email',
        name: 'Escalation Email',
        config: { recipients: [] },
      }),
    );
  });

  it('seeds syslog config and renders SyslogChannelForm when Syslog type is picked', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'c4', type: 'syslog', name: 'SIEM', enabled: true,
      config: { host: '', port: 514, protocol: 'udp', facility: 'local0' },
    });
    const user = userEvent.setup({ delay: null });
    renderTab();

    await screen.findByText('Ops Webhook');
    await user.click(screen.getByRole('button', { name: /add channel/i }));

    const typeSelect = await screen.findByLabelText(/^type$/i);
    await user.selectOptions(typeSelect, 'syslog');

    expect(screen.getByText(/syslog configuration/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^host$/i)).toBeInTheDocument();

    const nameInput = screen.getByLabelText(/^name$/i);
    await user.click(nameInput);
    await user.type(nameInput, 'SIEM');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/alerts/channels', {
        type: 'syslog',
        name: 'SIEM',
        config: { host: '', port: 514, protocol: 'udp', facility: 'local0' },
      }),
    );
  });

  it('renders a test success without "HTTP" when response_code is null', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      response_code: null,
      error: null,
    });
    const user = userEvent.setup({ delay: null });
    renderTab();

    await screen.findByText('Ops Webhook');
    await user.click(screen.getByRole('button', { name: /^test$/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/alerts/channels/c1/test'),
    );
    const status = await screen.findByText(/test succeeded/i);
    expect(status.textContent).not.toMatch(/HTTP/);
  });

  it('deletes a channel via in-page confirm (never window.confirm)', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm');
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({});
    const user = userEvent.setup({ delay: null });
    renderTab();

    await screen.findByText('Ops Webhook');
    await user.click(screen.getByRole('button', { name: /^delete ops webhook$/i }));

    // In-page confirm dialog, never the native confirm()
    expect(confirmSpy).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() =>
      expect(apiClient.del).toHaveBeenCalledWith('/alerts/channels/c1'),
    );
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});
