// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { LdapSettingsTab } from '../LdapSettingsTab';
import { apiClient } from '../../../api/client';

// Mock the raw API client (used by CategorySettings internals)
vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

// Mock useTestLdap so we don't exercise the real mutation machinery
const mockMutate = vi.fn();
const mockReset = vi.fn();

vi.mock('../../../api/auth', () => ({
  useTestLdap: vi.fn(),
}));

import { useTestLdap } from '../../../api/auth';

// Helper: default idle state for useTestLdap
function mockIdleTestLdap() {
  (useTestLdap as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: mockMutate,
    reset: mockReset,
    isPending: false,
    data: undefined,
    error: null,
  });
}

// CategorySettings makes two GET requests: /settings/:category and /settings/schema.
// `enabled` is hidden — the tab owns it as its master switch — while the rest of
// the category renders through the generic grid.
const MOCK_SCHEMA = {
  schema: {
    ldap: {
      enabled: {
        type: 'boolean',
        default: false,
        description: 'Master toggle for LDAP authentication.',
        hidden: true,
      },
      server_url: {
        type: 'string',
        default: '',
        description: 'LDAP server URL (ldap://host:389 or ldaps://host:636).',
        hidden: false,
      },
      use_ssl: {
        type: 'boolean',
        default: true,
        description: 'Use LDAPS (TLS).',
        hidden: false,
      },
      group_to_role_mapping: {
        type: 'json',
        default: {},
        description: 'JSON object mapping LDAP group DN to AssetAtlas role.',
        hidden: false,
      },
    },
  },
};

const DEFAULT_SETTINGS = {
  enabled: { value: false },
  server_url: { value: '' },
  use_ssl: { value: true },
  group_to_role_mapping: { value: {} },
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
  screen.findByRole('switch', { name: /enable ldap authentication/i });

describe('LdapSettingsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockIdleTestLdap();
    setupApiMocks();
  });

  it('renders the LDAP category settings form', async () => {
    wrap(<LdapSettingsTab />);
    // The help text sits in the card header, once the settings have loaded
    expect(await screen.findByText(/configure ldap/i)).toBeInTheDocument();
    expect(screen.getByText('Group To Role Mapping')).toBeInTheDocument();
    // Test connection button is always rendered
    expect(screen.getByRole('button', { name: /test connection/i })).toBeInTheDocument();
  });

  it('opens the test dialog when Test connection is clicked', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<LdapSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test connection/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it('calls useTestLdap.mutate with entered credentials on submit', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<LdapSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test connection/i }));

    await user.type(screen.getByLabelText(/username/i), 'jsmith');
    await user.type(screen.getByLabelText(/password/i), 'secret');
    await user.click(screen.getByRole('button', { name: /^test$/i }));

    expect(mockMutate).toHaveBeenCalledWith({ username: 'jsmith', password: 'secret' });
  });

  it('shows user_dn and mapped_role on success', async () => {
    (useTestLdap as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate: mockMutate,
      reset: mockReset,
      isPending: false,
      data: {
        success: true,
        user_dn: 'CN=jsmith,OU=Users,DC=corp,DC=com',
        mapped_role: 'admin',
        attributes: { mail: 'jsmith@corp.com' },
        errors: [],
      },
      error: null,
    });

    const user = userEvent.setup({ delay: null });
    wrap(<LdapSettingsTab />);

    // Open dialog so the result is visible
    await user.click(await screen.findByRole('button', { name: /test connection/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
    expect(screen.getByText(/CN=jsmith,OU=Users,DC=corp,DC=com/)).toBeInTheDocument();
    // The role badge spans are inside the status element; there are multiple 'admin' text nodes
    // (one in help block, one in badge) — check via the badge class
    expect(screen.getByRole('status')).toHaveTextContent('admin');
  });

  it('shows errors list on failure', async () => {
    (useTestLdap as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate: mockMutate,
      reset: mockReset,
      isPending: false,
      data: {
        success: false,
        user_dn: null,
        mapped_role: null,
        attributes: {},
        errors: ['Invalid credentials', 'LDAP bind failed'],
      },
      error: null,
    });

    const user = userEvent.setup({ delay: null });
    wrap(<LdapSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test connection/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
    expect(screen.getByText(/Invalid credentials/)).toBeInTheDocument();
    expect(screen.getByText(/LDAP bind failed/)).toBeInTheDocument();
  });

  it('closes the dialog when Close is clicked', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<LdapSettingsTab />);

    await user.click(await screen.findByRole('button', { name: /test connection/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  // ── Master toggle (#1149) ─────────────────────────────────────────────

  it('renders the master toggle and does not repeat it as a grid row', async () => {
    wrap(<LdapSettingsTab />);

    expect(await masterToggle()).toBeInTheDocument();
    // hidden=True in settings_schema keeps the generic renderer from drawing
    // an "Enabled" row for the same key.
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument();
  });

  it('commits the master toggle on Save, not on click', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<LdapSettingsTab />);

    await user.click(await masterToggle());
    expect(apiClient.put).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(apiClient.put).toHaveBeenCalledWith('/settings/ldap/enabled', {
        value: true,
        reason: 'Updated from Settings UI',
      });
    });
  });

  it('reports the effective state, not just the flag, when config is missing', async () => {
    // enabled with no server_url is exactly what LdapService.is_enabled()
    // rejects: the tab reads "on" while the directory is never contacted.
    setupApiMocks({ enabled: { value: true } });
    wrap(<LdapSettingsTab />);

    expect(
      await screen.findByText(/Enabled, but incomplete — Server URL not configured\./),
    ).toBeInTheDocument();
  });

  it('reports LDAP as live once the server URL is configured', async () => {
    setupApiMocks({
      enabled: { value: true },
      server_url: { value: 'ldaps://dc.corp.com:636' },
    });
    wrap(<LdapSettingsTab />);

    expect(
      await screen.findByText(/Enabled — sign-ins are checked against the directory first\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/but incomplete/)).not.toBeInTheDocument();
  });

  it('tracks the draft: flipping the toggle updates the effective-state note', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<LdapSettingsTab />);

    // Off, so the note describes the disabled state.
    expect(
      await screen.findByText(/Disabled — sign-ins are checked against local passwords only\./),
    ).toBeInTheDocument();

    await user.click(await masterToggle());

    // Still unsaved, but the note already reflects what Save would produce —
    // and says so, so the present tense isn't read as "already applied".
    expect(
      screen.getByText(/Enabled, but incomplete — Server URL not configured\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/Not applied until you press Save\./)).toBeInTheDocument();
  });

  it('flags the unsaved cue when a required field — not the switch — is the pending edit', async () => {
    // The note is derived from every required key, so the cue has to be too:
    // filling in a blank Server URL flips it to "live" with nothing saved.
    // The mechanism lives in CategorySettings, so OIDC shares this behaviour.
    const user = userEvent.setup({ delay: null });
    setupApiMocks({ enabled: { value: true } });
    wrap(<LdapSettingsTab />);

    expect(
      await screen.findByText(/Enabled, but incomplete — Server URL not configured\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Not applied until you press Save\./)).not.toBeInTheDocument();

    // String rows render before the JSON block, so the first textbox is
    // server_url and the second is the group_to_role_mapping textarea.
    const [serverUrl] = screen.getAllByRole('textbox');
    await user.type(serverUrl, 'ldaps://dc.corp.com');

    expect(
      screen.getByText(
        /Enabled — sign-ins are checked against the directory first\. Not applied until you press Save\./,
      ),
    ).toBeInTheDocument();
  });
});
