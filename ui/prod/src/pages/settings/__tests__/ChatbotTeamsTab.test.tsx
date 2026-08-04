// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ChatbotTeamsTab } from '../ChatbotTeamsTab';
import { apiClient } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  apiClient: {
    get: vi.fn(),
    put: vi.fn(),
    post: vi.fn(),
    del: vi.fn(),
    download: vi.fn(),
    resolveUrl: (p: string) => p,
  },
}));

const BASE_SETTINGS = {
  enabled: false,
  bot_app_id: '',
  bot_app_password: '' as const,
  bot_app_tenant_id: '',
  messaging_endpoint: '',
};

const HTTPS_ENDPOINT = {
  url: 'https://assetatlas.example.com/teams/api/messages',
  https_ok: true,
  is_override: false,
};

// Fully configured: enabled, valid UUIDs, stored secret, HTTPS endpoint —
// Test connection and Download start out enabled.
const CONFIGURED_SETTINGS = {
  ...BASE_SETTINGS,
  enabled: true,
  bot_app_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  bot_app_password: '__SET__' as const,
  bot_app_tenant_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
};

function setupApi(opts: {
  settings?: typeof BASE_SETTINGS;
  endpoint?: typeof HTTPS_ENDPOINT;
} = {}) {
  const settings = opts.settings ?? BASE_SETTINGS;
  const endpoint = opts.endpoint ?? HTTPS_ENDPOINT;
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation(
    (url: string) => {
      if (url === '/settings/teams') return Promise.resolve(settings);
      if (url === '/settings/teams/messaging-endpoint') {
        return Promise.resolve(endpoint);
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    },
  );
  (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({});
  (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
}

// Renders the tab fully configured and waits for Test connection to enable.
async function renderConfigured() {
  setupApi({ settings: CONFIGURED_SETTINGS });
  const user = userEvent.setup();
  render(<ChatbotTeamsTab />);
  const testBtn = await screen.findByRole('button', {
    name: /test connection/i,
  });
  await waitFor(() => expect(testBtn).not.toBeDisabled());
  return { user, testBtn };
}

describe('ChatbotTeamsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupApi();
  });

  it('renders the master toggle off by default', async () => {
    render(<ChatbotTeamsTab />);
    const toggle = await screen.findByLabelText(/Enable Microsoft Teams/i);
    expect(toggle).not.toBeChecked();
  });

  it('renders the messaging endpoint as the input placeholder', async () => {
    render(<ChatbotTeamsTab />);
    await waitFor(() => {
      // When messaging_endpoint is empty, the resolved URL is shown as the
      // editable input's placeholder (computed default).
      const input = screen.getByLabelText(
        /Messaging endpoint URL/i,
      ) as HTMLInputElement;
      expect(input.placeholder).toBe(
        'https://assetatlas.example.com/teams/api/messages',
      );
      expect(input.value).toBe('');
    });
  });

  it('shows HTTPS warning when endpoint is not https', async () => {
    setupApi({
      endpoint: {
        url: 'http://localhost:5001/teams/api/messages',
        https_ok: false,
        is_override: false,
      },
    });
    render(<ChatbotTeamsTab />);
    await waitFor(() => {
      expect(screen.getByText(/must be HTTPS/i)).toBeInTheDocument();
    });
  });

  it('disables Download until enabled+creds+https', async () => {
    render(<ChatbotTeamsTab />);
    const btn = await screen.findByRole('button', {
      name: /download teams app/i,
    });
    expect(btn).toBeDisabled();
  });

  it('disables Test connection when public URL is not HTTPS', async () => {
    setupApi({
      settings: CONFIGURED_SETTINGS,
      endpoint: {
        url: 'http://localhost:5001/teams/api/messages',
        https_ok: false,
        is_override: false,
      },
    });
    render(<ChatbotTeamsTab />);
    const btn = await screen.findByRole('button', {
      name: /test connection/i,
    });
    expect(btn).toBeDisabled();
  });

  it('toggling enabled fires PUT /settings/teams/enabled', async () => {
    const user = userEvent.setup();
    render(<ChatbotTeamsTab />);
    const toggle = await screen.findByLabelText(/Enable Microsoft Teams/i);
    await user.click(toggle);
    await waitFor(() => {
      expect(apiClient.put).toHaveBeenCalledWith('/settings/teams/enabled', {
        value: true,
      });
    });
  });

  it('puts the switch back when the PUT is rejected', async () => {
    const user = userEvent.setup();
    (apiClient.put as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom'),
    );
    render(<ChatbotTeamsTab />);
    const toggle = await screen.findByLabelText(/Enable Microsoft Teams/i);
    await user.click(toggle);
    // Nothing was stored, so neither the switch nor the note beneath it may
    // claim the bridge is on.
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(
      screen.getByText(/Disabled — the bridge is idle/),
    ).toBeInTheDocument();
  });

  it('saves the messaging endpoint override on blur', async () => {
    setupApi({ settings: CONFIGURED_SETTINGS });
    const user = userEvent.setup();
    render(<ChatbotTeamsTab />);
    const input = (await screen.findByLabelText(
      /Messaging endpoint URL/i,
    )) as HTMLInputElement;
    expect(input).not.toBeDisabled();
    await user.click(input);
    await user.keyboard('https://override.example.com/api/messages');
    // Blur by tabbing away.
    await user.tab();
    await waitFor(() => {
      expect(apiClient.put).toHaveBeenCalledWith(
        '/settings/teams/messaging_endpoint',
        { value: 'https://override.example.com/api/messages' },
      );
    });
  });

  it('rejects a non-HTTPS messaging endpoint without firing PUT', async () => {
    setupApi({ settings: CONFIGURED_SETTINGS });
    const user = userEvent.setup();
    render(<ChatbotTeamsTab />);
    const input = (await screen.findByLabelText(
      /Messaging endpoint URL/i,
    )) as HTMLInputElement;
    await user.click(input);
    await user.keyboard('http://insecure.example.com/api/messages');
    await user.tab();
    await waitFor(() => {
      expect(screen.getByText(/must be HTTPS or empty/i)).toBeInTheDocument();
    });
    expect(apiClient.put).not.toHaveBeenCalledWith(
      '/settings/teams/messaging_endpoint',
      expect.anything(),
    );
  });

  it('clicking Download fetches the manifest as a blob', async () => {
    setupApi({ settings: CONFIGURED_SETTINGS });
    const blob = new Blob(['zip-content'], { type: 'application/zip' });
    (apiClient.download as ReturnType<typeof vi.fn>).mockResolvedValue({
      blob,
      filename: 'assetatlas-teams.zip',
    });
    const createObjectURL = vi.fn(() => 'blob:mock-url');
    const revokeObjectURL = vi.fn();
    // jsdom doesn't provide URL.createObjectURL — stub it.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    });

    const user = userEvent.setup();
    render(<ChatbotTeamsTab />);
    const btn = await screen.findByRole('button', {
      name: /download teams app/i,
    });
    await waitFor(() => expect(btn).not.toBeDisabled());
    await user.click(btn);

    await waitFor(() => {
      expect(apiClient.download).toHaveBeenCalledWith(
        '/settings/teams/manifest.zip',
      );
    });
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
  });

  // #1226 — Test connection must describe what is on screen.

  it('sends the typed secret with the test request', async () => {
    const { user, testBtn } = await renderConfigured();

    await user.click(
      screen.getByRole('button', { name: /edit client secret/i }),
    );
    await user.type(
      screen.getByLabelText(/client secret/i), 'freshly-rotated',
    );
    await user.click(testBtn);

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/settings/teams/test-connection',
        {
          bot_app_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
          bot_app_tenant_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
          bot_app_password: 'freshly-rotated',
        },
      );
    });
    expect(screen.getByText(/Connection ok/i)).toBeInTheDocument();
  });

  it('omits the secret from the test request when testing the stored one', async () => {
    const { user, testBtn } = await renderConfigured();
    await user.click(testBtn);

    await waitFor(() => {
      // Exact body: no bot_app_password key, so the server tests the
      // stored secret — the only one that exists in this state.
      expect(apiClient.post).toHaveBeenCalledWith(
        '/settings/teams/test-connection',
        {
          bot_app_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
          bot_app_tenant_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        },
      );
    });
  });

  it('keeps Test and Download disabled while the new-secret box is empty', async () => {
    const { user, testBtn } = await renderConfigured();
    const downloadBtn = screen.getByRole('button', {
      name: /download teams app/i,
    });

    // Opening the editor leaves an empty box — nothing on screen to test.
    await user.click(
      screen.getByRole('button', { name: /edit client secret/i }),
    );
    expect(testBtn).toBeDisabled();
    expect(downloadBtn).toBeDisabled();

    await user.type(screen.getByLabelText(/client secret/i), 's');
    expect(testBtn).not.toBeDisabled();
    expect(downloadBtn).not.toBeDisabled();
  });

  it('clears the shown test result when the secret is edited', async () => {
    const { user, testBtn } = await renderConfigured();
    await user.click(testBtn);
    await screen.findByText(/Connection ok/i);

    await user.click(
      screen.getByRole('button', { name: /edit client secret/i }),
    );
    expect(screen.queryByText(/Connection ok/i)).not.toBeInTheDocument();
  });

  it('clears the shown test result when an ID is changed', async () => {
    const { user, testBtn } = await renderConfigured();
    await user.click(testBtn);
    await screen.findByText(/Connection ok/i);

    const idInput = screen.getByLabelText(/Application \(client\) ID/i);
    await user.clear(idInput);
    await user.type(idInput, 'cccccccc-cccc-cccc-cccc-cccccccccccc');
    await user.tab();
    await waitFor(() => {
      expect(screen.queryByText(/Connection ok/i)).not.toBeInTheDocument();
    });
  });

  it('clears the shown test result when an ID edit is rejected as invalid', async () => {
    const { user, testBtn } = await renderConfigured();
    await user.click(testBtn);
    await screen.findByText(/Connection ok/i);

    // The (uncontrolled) input keeps showing the rejected text, so the
    // verdict no longer describes what is on screen and must go.
    const idInput = screen.getByLabelText(/Application \(client\) ID/i);
    await user.clear(idInput);
    await user.type(idInput, 'not-a-uuid');
    await user.tab();
    await waitFor(() => {
      expect(screen.queryByText(/Connection ok/i)).not.toBeInTheDocument();
    });
    expect(screen.getByText(/must be a UUID/i)).toBeInTheDocument();
  });

  it('discards a test verdict that resolves after the secret changed', async () => {
    const { user, testBtn } = await renderConfigured();
    let resolveTest: (v: { ok: boolean }) => void = () => {};
    (apiClient.post as ReturnType<typeof vi.fn>).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveTest = resolve;
        }),
    );
    await user.click(testBtn);

    // Edit a credential while the probe is still in flight…
    await user.click(
      screen.getByRole('button', { name: /edit client secret/i }),
    );
    await user.type(screen.getByLabelText(/client secret/i), 'other-secret');
    // …then let the stale probe settle: its verdict must not surface.
    resolveTest({ ok: true });
    await waitFor(() => expect(testBtn).not.toBeDisabled());
    expect(screen.queryByText(/Connection ok/i)).not.toBeInTheDocument();
  });

  it('keeps Save disabled for a whitespace-only secret', async () => {
    const { user } = await renderConfigured();
    await user.click(
      screen.getByRole('button', { name: /edit client secret/i }),
    );
    await user.type(screen.getByLabelText(/client secret/i), '   ');
    // Same trim rule as Test: a secret Test refuses to probe must not be
    // saveable either.
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
  });

  // #1227 — the switch must say what it actually achieved, the way the
  // LDAP/OIDC master toggles do since #1149.

  it('reports the disabled bridge next to the switch', async () => {
    render(<ChatbotTeamsTab />);
    expect(
      await screen.findByText(/Disabled — the bridge is idle/),
    ).toBeInTheDocument();
  });

  it('names every missing credential when the switch is on', async () => {
    setupApi({ settings: { ...BASE_SETTINGS, enabled: true } });
    render(<ChatbotTeamsTab />);
    expect(
      await screen.findByText(
        /Enabled, but incomplete — Application \(client\) ID, Client secret and Tenant ID not configured\./,
      ),
    ).toBeInTheDocument();
  });

  it('names only the credential that is missing', async () => {
    setupApi({
      settings: { ...CONFIGURED_SETTINGS, bot_app_password: '' as const },
    });
    render(<ChatbotTeamsTab />);
    expect(
      await screen.findByText(
        /Enabled, but incomplete — Client secret not configured\./,
      ),
    ).toBeInTheDocument();
  });

  it('reports a non-HTTPS public URL as the reason the buttons are off', async () => {
    setupApi({
      settings: CONFIGURED_SETTINGS,
      endpoint: {
        url: 'http://localhost:5001/teams/api/messages',
        https_ok: false,
        is_override: false,
      },
    });
    render(<ChatbotTeamsTab />);
    // Same condition as the buttons: the note explains their greyed-out state
    // rather than describing a bridge the tab knows is inert.
    expect(
      await screen.findByText(
        /Enabled, but incomplete — the public URL is not HTTPS\./,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /download teams app/i }),
    ).toBeDisabled();
  });

  it('confirms the bridge is live once everything is in place', async () => {
    await renderConfigured();
    expect(
      screen.getByText(/Enabled — the bridge answers messages in Microsoft Teams\./),
    ).toBeInTheDocument();
  });

  it('updates the note as soon as the switch is flipped, with no Save', async () => {
    const user = userEvent.setup();
    render(<ChatbotTeamsTab />);
    await user.click(await screen.findByLabelText(/Enable Microsoft Teams/i));
    // The switch commits on click, so the note describes the stored state and
    // must never carry the "Not applied until you press Save." clause.
    const note = await screen.findByText(/Enabled, but incomplete —/);
    expect(note).not.toHaveTextContent(/Not applied/);
  });

  it('flags a typed secret as not applied until its Save is pressed', async () => {
    const { user } = await renderConfigured();
    await user.click(
      screen.getByRole('button', { name: /edit client secret/i }),
    );
    // An empty box is a missing credential, not a pending one.
    expect(
      screen.getByText(/Enabled, but incomplete — Client secret not configured\./),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText(/client secret/i), 'freshly-rotated');
    expect(
      screen.getByText(/Not applied until you press Save\./),
    ).toBeInTheDocument();
  });
});
