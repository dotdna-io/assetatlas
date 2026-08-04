// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { MonitoringSettingsTab } from '../MonitoringSettingsTab';
import { apiClient } from '../../../api/client';
import type { ForwardingConfig, ForwardingConfigSaveResult } from '../../../api/metricsForwarding';

// Mock the transport only — the real metricsForwardingApi + getApiErrorMessage
// run unmocked (importOriginal), matching AlertChannelsTab.test.tsx.
vi.mock('../../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/client')>();
  return {
    ...actual,
    apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
  };
});

const CONFIG_DISABLED: ForwardingConfig = {
  endpoint_url: null,
  auth_type: 'none',
  username: null,
  external_labels: {},
  enabled: false,
  has_secret: false,
  updated_at: null,
  updated_by: null,
};

const CONFIG_BEARER: ForwardingConfig = {
  endpoint_url: 'https://prom.example.com/api/v1/write',
  auth_type: 'bearer',
  username: null,
  external_labels: { cluster: 'prod' },
  enabled: true,
  has_secret: true,
  updated_at: '2026-07-11T00:00:00Z',
  updated_by: 'admin',
};

type Mock = ReturnType<typeof vi.fn>;
const getMock = () => apiClient.get as Mock;
const putMock = () => apiClient.put as Mock;
const postMock = () => apiClient.post as Mock;

function mockGet(config: ForwardingConfig) {
  getMock().mockResolvedValue(config);
}

// A representative save response (read view + apply block) so formFromConfig
// doesn't crash after a save; `over` tweaks the apply/enabled for each case.
function saveResult(over: Partial<ForwardingConfigSaveResult> = {}): ForwardingConfigSaveResult {
  return {
    ...CONFIG_BEARER,
    apply: {
      enabled: true,
      config_written: true,
      config_path: '/data/metrics-forwarder/agent.yml',
      reload: { attempted: true, ok: true, status_code: 200, detail: 'agent reloaded' },
    },
    ...over,
  };
}

function renderTab() {
  return render(<MonitoringSettingsTab />);
}

describe('MonitoringSettingsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet(CONFIG_DISABLED);
  });

  it('loads the config from the API on mount', async () => {
    mockGet(CONFIG_BEARER);
    renderTab();

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith('/metrics-forwarding/config'),
    );
    expect(
      await screen.findByDisplayValue('https://prom.example.com/api/v1/write'),
    ).toBeInTheDocument();
    const enable = screen.getByLabelText(/enable metrics forwarding/i) as HTMLInputElement;
    expect(enable.checked).toBe(true);
    // external label round-trips into the editor
    expect(screen.getByDisplayValue('cluster')).toBeInTheDocument();
    expect(screen.getByDisplayValue('prod')).toBeInTheDocument();
  });

  it('never renders a stored secret — shows a "(saved)" placeholder instead', async () => {
    mockGet(CONFIG_BEARER);
    renderTab();

    const token = (await screen.findByLabelText(/bearer token/i)) as HTMLInputElement;
    expect(token.value).toBe('');
    expect(token.placeholder).toMatch(/saved/i);
  });

  it('shows conditional auth fields per auth type', async () => {
    mockGet(CONFIG_DISABLED);
    const user = userEvent.setup();
    renderTab();
    const authSelect = await screen.findByLabelText(/authentication/i);

    // none -> no secret/username fields
    expect(screen.queryByLabelText(/bearer token/i)).toBeNull();
    expect(screen.queryByLabelText(/^password$/i)).toBeNull();

    // bearer -> token, no username
    await user.selectOptions(authSelect, 'bearer');
    expect(screen.getByLabelText(/bearer token/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^username$/i)).toBeNull();

    // basic -> username + password, no bearer token
    await user.selectOptions(authSelect, 'basic');
    expect(screen.getByLabelText(/^username$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/bearer token/i)).toBeNull();
  });

  it('keeps the stored secret on save when the token field is untouched (omits auth_secret)', async () => {
    mockGet(CONFIG_BEARER);
    putMock().mockResolvedValue(saveResult());
    const user = userEvent.setup();
    renderTab();

    const endpoint = await screen.findByDisplayValue('https://prom.example.com/api/v1/write');
    await user.clear(endpoint);
    await user.type(endpoint, 'https://new.example.com/api/v1/write');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    const [url, body] = putMock().mock.calls[0];
    expect(url).toBe('/metrics-forwarding/config');
    expect(body).not.toHaveProperty('auth_secret');
    expect(body.endpoint_url).toBe('https://new.example.com/api/v1/write');
    expect(body.auth_type).toBe('bearer');
    expect(body.external_labels).toEqual({ cluster: 'prod' });
  });

  it('sends auth_secret only when the admin types a new token', async () => {
    mockGet(CONFIG_BEARER);
    putMock().mockResolvedValue(saveResult());
    const user = userEvent.setup();
    renderTab();

    const token = (await screen.findByLabelText(/bearer token/i)) as HTMLInputElement;
    await user.type(token, 'new-secret-token');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    expect(putMock().mock.calls[0][1].auth_secret).toBe('new-secret-token');
  });

  it('a clean save shows no confirmation banner — the form quietly reflects the saved state', async () => {
    mockGet(CONFIG_BEARER);
    putMock().mockResolvedValue(saveResult());
    const user = userEvent.setup();
    renderTab();

    await screen.findByDisplayValue('https://prom.example.com/api/v1/write');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(putMock()).toHaveBeenCalled());
    expect(screen.queryByText(/agent was reloaded/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^saved\.?$/i)).not.toBeInTheDocument();
  });

  it('reports when the agent is unreachable (config applies on next start)', async () => {
    mockGet(CONFIG_BEARER);
    putMock().mockResolvedValue(
      saveResult({
        apply: {
          enabled: true,
          config_written: true,
          reload: { attempted: true, ok: false, error: 'connection refused', detail: 'agent unreachable' },
        },
      }),
    );
    const user = userEvent.setup();
    renderTab();

    await screen.findByDisplayValue('https://prom.example.com/api/v1/write');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    const banner = await screen.findByText(/unreachable/i);
    expect(banner).toHaveTextContent(/next start/i);
  });

  it('saving with the toggle off sends enabled:false and shows no banner', async () => {
    mockGet(CONFIG_BEARER);
    putMock().mockResolvedValue(
      saveResult({
        enabled: false,
        apply: { enabled: false, config_removed: true, reload: { attempted: false, detail: 'forwarding disabled' } },
      }),
    );
    const user = userEvent.setup();
    renderTab();

    const enable = (await screen.findByLabelText(/enable metrics forwarding/i)) as HTMLInputElement;
    await user.click(enable); // uncheck
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    expect(putMock().mock.calls[0][1].enabled).toBe(false);
    expect(screen.queryByText(/disabled/i)).not.toBeInTheDocument();
  });

  it('runs a connection test against the saved endpoint', async () => {
    mockGet(CONFIG_BEARER);
    postMock().mockResolvedValue({ reachable: true, status_code: 204, detail: 'ok' });
    const user = userEvent.setup();
    renderTab();

    await screen.findByDisplayValue('https://prom.example.com/api/v1/write');
    await user.click(screen.getByRole('button', { name: /test connection/i }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/metrics-forwarding/config/test'),
    );
    expect(await screen.findByText(/reachable/i)).toBeInTheDocument();
  });

  it('shows a failure when the endpoint is not reachable', async () => {
    mockGet(CONFIG_BEARER);
    postMock().mockResolvedValue({ reachable: false, detail: 'could not reach the endpoint' });
    const user = userEvent.setup();
    renderTab();

    await screen.findByDisplayValue('https://prom.example.com/api/v1/write');
    await user.click(screen.getByRole('button', { name: /test connection/i }));
    expect(await screen.findByText(/could not reach the endpoint/i)).toBeInTheDocument();
  });

  it('requires an endpoint URL before enabling (no PUT fired)', async () => {
    mockGet(CONFIG_DISABLED);
    const user = userEvent.setup();
    renderTab();

    const enable = await screen.findByLabelText(/enable metrics forwarding/i);
    await user.click(enable); // enable, but URL still blank
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText(/endpoint url is required/i)).toBeInTheDocument();
    expect(apiClient.put).not.toHaveBeenCalled();
  });

  it('adds an external label row and includes it on save', async () => {
    mockGet(CONFIG_DISABLED);
    putMock().mockResolvedValue(
      saveResult({ enabled: false, external_labels: { env: 'staging' }, apply: { enabled: false, config_removed: true } }),
    );
    const user = userEvent.setup();
    renderTab();

    await screen.findByLabelText(/authentication/i);
    expect(screen.getByText(/no external labels/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /add label/i }));
    await user.type(screen.getByLabelText(/label 1 name/i), 'env');
    await user.type(screen.getByLabelText(/label 1 value/i), 'staging');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    expect(putMock().mock.calls[0][1].external_labels).toEqual({ env: 'staging' });
  });

  it('removes an external label row', async () => {
    mockGet(CONFIG_BEARER); // one row: cluster=prod
    const user = userEvent.setup();
    renderTab();

    await screen.findByDisplayValue('cluster');
    await user.click(screen.getByRole('button', { name: /remove label 1/i }));
    expect(screen.getByText(/no external labels/i)).toBeInTheDocument();
  });
});
