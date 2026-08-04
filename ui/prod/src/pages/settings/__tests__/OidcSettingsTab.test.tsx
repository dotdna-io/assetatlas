// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { OidcSettingsTab } from '../OidcSettingsTab';
import { apiClient } from '../../../api/client';

// Mock the raw API client (used by CategorySettings internals)
vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

// Mock useTestOidcDiscovery so we don't exercise the real mutation machinery
const mockMutate = vi.fn();
const mockReset = vi.fn();

vi.mock('../../../api/auth', () => ({
  useTestOidcDiscovery: vi.fn(),
}));

import { useTestOidcDiscovery } from '../../../api/auth';

// Helper: default idle state for useTestOidcDiscovery
function mockIdleTestDiscovery() {
  (useTestOidcDiscovery as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: mockMutate,
    reset: mockReset,
    isPending: false,
    data: undefined,
    error: null,
  });
}

// CategorySettings makes two GETs: /settings/:category and /settings/schema.
// `enabled` is hidden — the tab owns it as its master switch.
const MOCK_SCHEMA = {
  schema: {
    oidc: {
      enabled: {
        type: 'boolean',
        default: false,
        description: 'Master toggle for OIDC SSO authentication.',
        hidden: true,
      },
      discovery_url: {
        type: 'string',
        default: '',
        description: 'OIDC discovery document URL.',
        hidden: false,
      },
      client_id: {
        type: 'string',
        default: '',
        description: 'OIDC application (client) ID.',
        hidden: false,
      },
      client_secret: {
        type: 'string',
        default: '',
        description: 'OIDC client secret.',
        hidden: false,
      },
      redirect_uri: {
        type: 'string',
        default: '',
        description: 'Public callback URL registered in the IdP.',
        hidden: false,
      },
      group_to_role_mapping: {
        type: 'json',
        default: {},
        description: 'JSON object mapping group claim values to AssetAtlas roles.',
        hidden: false,
      },
    },
  },
};

const DEFAULT_SETTINGS = {
  enabled: { value: false },
  discovery_url: { value: '' },
  client_id: { value: '' },
  client_secret: { value: '' },
  redirect_uri: { value: '' },
  group_to_role_mapping: { value: {} },
};

// A fully-configured IdP. client_secret comes back as the stored ciphertext,
// which is what the settings API actually returns for an encrypted key.
const CONFIGURED = {
  discovery_url: { value: 'https://idp.example.com/.well-known/openid-configuration' },
  client_id: { value: '00000000-0000-0000-0000-000000000001' },
  client_secret: { value: 'ENC:abc123' },
  redirect_uri: { value: 'https://app.example.com/api/auth/oidc/callback' },
};

function setupApiMocks(overrides: Record<string, { value: unknown }> = {}) {
  const settings = { ...DEFAULT_SETTINGS, ...overrides };
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.includes('/schema')) return Promise.resolve(MOCK_SCHEMA);
    return Promise.resolve({ settings });
  });
  (apiClient.put as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      {ui}
    </QueryClientProvider>,
  );
}

const masterToggle = () =>
  screen.findByRole('switch', { name: /enable oidc single sign-on/i });

describe('OidcSettingsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockIdleTestDiscovery();
    setupApiMocks();
  });

  it('renders the OIDC category settings form', async () => {
    wrap(<OidcSettingsTab />);
    // Help text sits in the card header, once the settings have loaded
    expect(await screen.findByText(/configure openid connect/i)).toBeInTheDocument();
    expect(screen.getByText('Group To Role Mapping')).toBeInTheDocument();
    // Test discovery button always rendered
    expect(screen.getByRole('button', { name: /test discovery/i })).toBeInTheDocument();
  });

  it('opens modal and calls mutate when Test discovery is clicked', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<OidcSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test discovery/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(mockMutate).toHaveBeenCalledOnce();
  });

  it('shows issuer and endpoints on successful discovery', async () => {
    (useTestOidcDiscovery as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate: mockMutate,
      reset: mockReset,
      isPending: false,
      data: {
        success: true,
        issuer: 'https://idp.example.com',
        authorization_endpoint: 'https://idp.example.com/authorize',
        token_endpoint: 'https://idp.example.com/token',
        jwks_uri: 'https://idp.example.com/.well-known/jwks.json',
        errors: [],
      },
      error: null,
    });

    const user = userEvent.setup({ delay: null });
    wrap(<OidcSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test discovery/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
    expect(screen.getByText(/https:\/\/idp\.example\.com$/)).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/idp\.example\.com\/authorize/)).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/idp\.example\.com\/token/)).toBeInTheDocument();
    expect(screen.getByText(/\.well-known\/jwks\.json/)).toBeInTheDocument();
  });

  it('shows errors list on failed discovery', async () => {
    (useTestOidcDiscovery as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate: mockMutate,
      reset: mockReset,
      isPending: false,
      data: {
        success: false,
        issuer: null,
        authorization_endpoint: null,
        token_endpoint: null,
        jwks_uri: null,
        errors: ['Discovery URL unreachable', 'Connection timeout'],
      },
      error: null,
    });

    const user = userEvent.setup({ delay: null });
    wrap(<OidcSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test discovery/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
    expect(screen.getByText(/Discovery URL unreachable/)).toBeInTheDocument();
    expect(screen.getByText(/Connection timeout/)).toBeInTheDocument();
  });

  it('closes the modal when Close is clicked', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<OidcSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test discovery/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  // ── Master toggle (#1149) ─────────────────────────────────────────────

  it('renders the master toggle and does not repeat it as a grid row', async () => {
    wrap(<OidcSettingsTab />);

    expect(await masterToggle()).toBeInTheDocument();
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument();
  });

  it('commits the master toggle on Save, not on click', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<OidcSettingsTab />);

    await user.click(await masterToggle());
    expect(apiClient.put).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(apiClient.put).toHaveBeenCalledWith('/settings/oidc/enabled', {
        value: true,
        reason: 'Updated from Settings UI',
      });
    });
  });

  it('names every IdP parameter OidcService.is_enabled() still needs', async () => {
    setupApiMocks({ enabled: { value: true } });
    wrap(<OidcSettingsTab />);

    expect(
      await screen.findByText(
        /Enabled, but incomplete — Discovery URL, Client ID, Client secret and Redirect URI not configured\./,
      ),
    ).toBeInTheDocument();
  });

  it('narrows the warning to the parameter still missing', async () => {
    setupApiMocks({ enabled: { value: true }, ...CONFIGURED, client_secret: { value: '' } });
    wrap(<OidcSettingsTab />);

    expect(
      await screen.findByText(/Enabled, but incomplete — Client secret not configured\./),
    ).toBeInTheDocument();
  });

  it('reports SSO as live once every parameter is configured', async () => {
    setupApiMocks({ enabled: { value: true }, ...CONFIGURED });
    wrap(<OidcSettingsTab />);

    expect(
      await screen.findByText(
        /Enabled — the sign-in page offers SSO through your identity provider\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/but incomplete/)).not.toBeInTheDocument();
  });

  it('tracks the draft: flipping the toggle updates the effective-state note', async () => {
    const user = userEvent.setup({ delay: null });
    setupApiMocks(CONFIGURED);
    wrap(<OidcSettingsTab />);

    expect(
      await screen.findByText(/Disabled — the sign-in page shows no SSO button\./),
    ).toBeInTheDocument();

    await user.click(await masterToggle());

    // Still unsaved, but the note already reflects what Save would produce —
    // and says so, so the present tense isn't read as "already applied".
    expect(
      screen.getByText(/Enabled — the sign-in page offers SSO through your identity provider\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/Not applied until you press Save\./)).toBeInTheDocument();
  });
});
