// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { FieldRegistryTab } from '../FieldRegistryTab';
import { apiClient } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

const REGISTRY = { fields: [], total: 0 };

function mockConfig(providerAvailable: boolean, enabled = false) {
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.includes('/field-descriptions')) {
      return Promise.resolve({
        enabled, provider_available: providerAvailable, active_provider: 'anthropic',
      });
    }
    return Promise.resolve(REGISTRY);
  });
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('FieldRegistryTab AI toggle', () => {
  beforeEach(() => vi.clearAllMocks());

  it('disables the toggle and shows a hint when no provider is configured', async () => {
    mockConfig(false);
    wrap(<FieldRegistryTab />);
    const sw = await screen.findByRole('switch');
    expect(sw).toBeDisabled();
    expect(
      screen.getByText(/Configure a chatbot LLM provider/i),
    ).toBeInTheDocument();
  });

  it('enables the toggle when a provider is available', async () => {
    mockConfig(true);
    wrap(<FieldRegistryTab />);
    await screen.findByRole('switch');
    await waitFor(() => expect(screen.getByRole('switch')).not.toBeDisabled());
  });

  it('is ON by default when a provider is available (#927 auto-enable)', async () => {
    mockConfig(true, true); // provider available + default-on preference
    wrap(<FieldRegistryTab />);
    const sw = await screen.findByRole('switch');
    await waitFor(() => expect(sw).not.toBeDisabled());
    expect(sw).toBeChecked();
  });

  it('forces the toggle off and locks it when the provider is gone (#927, never stuck on)', async () => {
    mockConfig(false, true); // provider unavailable but the stored setting is ON
    wrap(<FieldRegistryTab />);
    const sw = await screen.findByRole('switch');
    expect(sw).toBeDisabled();       // locked — no provider
    expect(sw).not.toBeChecked();    // effective off, not stuck on
  });
});

describe('FieldRegistryTab synonym status-poll failure (fold-in fix)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('stops the spinner and surfaces an error when the status poll keeps failing', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('/field-descriptions')) {
        return Promise.resolve({
          enabled: false, provider_available: true, active_provider: 'anthropic',
        });
      }
      if (url.includes('/synonyms/status')) {
        return Promise.reject(new Error('status check failed'));
      }
      return Promise.resolve(REGISTRY);
    });
    (apiClient.post as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('/synonyms/regenerate')) {
        return Promise.resolve({ status: 'started', mode: 'full', message: 'Regenerating…' });
      }
      return Promise.resolve({});
    });

    wrap(<FieldRegistryTab />);

    // Open the confirm dialog, then confirm — this fires the mutation whose
    // onSuccess flips synonymsRunning to true and starts the status poll.
    const regenButton = await screen.findByRole('button', { name: 'Regenerate All' });
    fireEvent.click(regenButton);
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));

    // The poll (mocked to always reject, no retries) settles as an error;
    // the button must stop spinning instead of waiting forever for a
    // 'done' status that will never arrive.
    await waitFor(() => expect(regenButton).not.toBeDisabled());
    expect(screen.getByRole('alert')).toHaveTextContent(
      /could not track synonym regeneration status/i,
    );
  });
});

describe('FieldRegistryTab synonyms column', () => {
  beforeEach(() => vi.clearAllMocks());

  const REGISTRY_WITH_SYNS = {
    fields: [
      {
        id: 1, field_name: 'hostname', field_type: 'static',
        display_name: 'Hostname', description: 'computer name',
        description_source: 'hardcoded', data_source: null, searchable: true,
        updated_at: null,
        synonyms: ['computer name', 'host name', 'machine name', 'pc name', 'node'],
      },
      {
        id: 2, field_name: 'src_widget_s', field_type: 'extended',
        display_name: 'Widget', description: 'a widget',
        description_source: 'llm', data_source: 'Some Source', searchable: true,
        updated_at: null, synonyms: [],
      },
    ],
    total: 2,
  };

  function mockRegistry() {
    (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('/field-descriptions')) {
        return Promise.resolve({
          enabled: false, provider_available: true, active_provider: 'anthropic',
        });
      }
      return Promise.resolve(REGISTRY_WITH_SYNS);
    });
  }

  it('shows a preview + count and opens a modal listing every alias', async () => {
    mockRegistry();
    wrap(<FieldRegistryTab />);

    // Expand the Core group so its rows render (groups start collapsed).
    fireEvent.click(await screen.findByText('AssetAtlas Core'));

    // Preview shows the first two aliases and a +N for the remaining three.
    expect(screen.getByText(/computer name, host name/)).toBeInTheDocument();
    expect(screen.getByText('+3')).toBeInTheDocument();
    // An alias beyond the preview is not visible until the modal opens.
    expect(screen.queryByText('node')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('+3'));

    // Modal lists all aliases, including the previously-hidden one.
    await waitFor(() => expect(screen.getByText('node')).toBeInTheDocument());
    expect(screen.getByText(/Synonyms for hostname/)).toBeInTheDocument();
  });

  it('renders an em dash for a field with no synonyms', async () => {
    mockRegistry();
    wrap(<FieldRegistryTab />);
    fireEvent.click(await screen.findByText('Some Source'));
    // The widget row's synonyms cell shows the empty marker.
    const rows = screen.getAllByRole('row');
    const widgetRow = rows.find((r) => r.textContent?.includes('src_widget_s'));
    expect(widgetRow).toBeTruthy();
    expect(widgetRow!.textContent).toContain('—');
  });
});

describe('FieldRegistryTab regenerate gating + endpoint', () => {
  beforeEach(() => vi.clearAllMocks());

  const REGISTRY_EXT = {
    fields: [
      {
        id: 2, field_name: 'src_widget_s', field_type: 'extended',
        display_name: 'Widget', description: 'a widget',
        description_source: 'hardcoded', data_source: 'Some Source',
        searchable: true, updated_at: null, synonyms: [],
      },
    ],
    total: 1,
  };

  function mockGet(providerAvailable: boolean) {
    (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes('/field-descriptions')) {
        return Promise.resolve({
          enabled: false, provider_available: providerAvailable,
          active_provider: providerAvailable ? 'anthropic' : '',
        });
      }
      return Promise.resolve(REGISTRY_EXT);
    });
  }

  it('disables Regenerate All and per-field Regen when no provider is configured', async () => {
    mockGet(false);
    wrap(<FieldRegistryTab />);
    // Global action is disabled…
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Regenerate All' })).toBeDisabled());
    // …and so is the per-field Regen (expand the data-source group first).
    fireEvent.click(await screen.findByText('Some Source'));
    expect(screen.getByRole('button', { name: 'Regen' })).toBeDisabled();
  });

  it('per-field Regen posts to /regenerate for that field', async () => {
    mockGet(true);
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      field_name: 'src_widget_s', description: 'x',
      description_source: 'llm', synonyms: ['widget'],
    });
    wrap(<FieldRegistryTab />);

    fireEvent.click(await screen.findByText('Some Source'));
    const regen = screen.getByRole('button', { name: 'Regen' });
    await waitFor(() => expect(regen).not.toBeDisabled());
    fireEvent.click(regen);
    // Confirm dialog → Regenerate (both description AND synonyms server-side).
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        '/settings/field-registry/src_widget_s/regenerate',
        undefined,
        { timeout: 90_000 },
      ));
  });
});
