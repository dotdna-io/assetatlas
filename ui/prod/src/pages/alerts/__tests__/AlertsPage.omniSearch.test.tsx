// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Alerts omni-search box (#908 / #903).
 *
 * The box unifies two filters that used to be separate and half-broken:
 *  - a title/description text search (was live-on-type; now Enter-triggered), and
 *  - an EXACT asset filter that reaches the server (was a client-side filter over
 *    only the first loaded page, so an asset's older alerts were silently missed
 *    while the asset-details header counted them all).
 *
 * Each test asserts the behaviour that makes the fix real, not just "a dropdown
 * appears": the asset filter must travel to the server as `asset_id` (so ALL the
 * asset's alerts load), the token must round-trip through the URL (so the
 * asset-details deep-link lands as a token), and text + asset must coexist. If a
 * future change re-imposed the old client-side filter, `asset_id` would stop
 * reaching the request and these fail.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Alert } from '@/api/alerts';

vi.mock('@/api/alerts', async () => {
  const actual = await vi.importActual<typeof import('@/api/alerts')>('@/api/alerts');
  return { ...actual, useAlerts: vi.fn() };
});
vi.mock('@/api/assets', async () => {
  const actual = await vi.importActual<typeof import('@/api/assets')>('@/api/assets');
  return { ...actual, useAssetSearch: vi.fn() };
});

import { useAlerts } from '@/api/alerts';
import { useAssetSearch } from '@/api/assets';
import AlertsPage from '../../AlertsPage';

const ASSET_ID = '948e3f69-818b-4649-8f77-362048686475';
const ASSET_RESULT = {
  id: ASSET_ID,
  hostname: 'gym-versant-80',
  primary_ip: '10.0.0.5',
  mac_address: null,
  serial_number: null,
  match_score: 1,
};

// Relative to now, not a literal date, so the alert survives the page's default
// 7-day window (a hardcoded date silently ages out — see the prometheus suite).
const RECENT = new Date(Date.now() - 3600_000).toISOString();

function makeAlert(): Alert {
  return {
    id: 'a-1', type: 'operational', severity: 'high', status: 'active',
    title: 'Base alert', description: null, reason: 'because', source: 'system',
    asset_count: 0, assets: [], assets_by_category: null, data_sources: [],
    subject_type: 'asset', subjects: null, threshold: null,
    created_at: RECENT, updated_at: RECENT, resolved_at: null,
  } as Alert;
}

// The params object AlertsPage handed to useAlerts on its most recent render —
// i.e. exactly what the next GET /api/alerts request will carry.
function lastAlertsParams(): Record<string, any> {
  const calls = (useAlerts as any).mock.calls;
  return (calls[calls.length - 1]?.[0] ?? {}) as Record<string, any>;
}

function renderPage(initialEntries: string[] = ['/alerts']) {
  (useAlerts as any).mockReturnValue({
    data: {
      alerts: [makeAlert()],
      summary: { total: 1, by_severity: {}, by_type: {}, by_status: {} },
      timeline: [],
      pagination: { page: 1, per_page: 20, total_items: 1, total_pages: 1 },
    },
    isLoading: false,
    error: null,
  });
  // Return the asset for any non-empty query; nothing for the empty (closed) one.
  (useAssetSearch as any).mockImplementation((p: { q?: string }) => ({
    data: p?.q
      ? { results: [ASSET_RESULT], count: 1, query: p.q }
      : { results: [], count: 0, query: '' },
    isLoading: false,
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        <AlertsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// The omni-input — queried by placeholder so it never collides with the native
// severity <select> (which also carries an implicit combobox role).
function omniInput() {
  return screen.getByPlaceholderText(/search alerts/i);
}

describe('AlertsPage — omni-search box (#908 / #903)', () => {
  beforeEach(() => {
    (useAlerts as any).mockReset();
    (useAssetSearch as any).mockReset();
  });

  it('surfaces server asset suggestions as you type', async () => {
    renderPage();
    fireEvent.change(omniInput(), { target: { value: 'gym' } });
    // After the debounce, the /assets/search result renders (hostname + ip).
    expect(await screen.findByText('gym-versant-80')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.5')).toBeInTheDocument();
  });

  it('picking a suggestion sets the token and sends asset_id to the server', async () => {
    renderPage();
    fireEvent.change(omniInput(), { target: { value: 'gym' } });
    fireEvent.click(await screen.findByText('gym-versant-80'));
    // The ✕ button is unique to the in-box token (a suggestion row has none).
    expect(screen.getByRole('button', { name: /clear asset filter/i })).toBeInTheDocument();
    // The fix: the id travels to the server, so ALL the asset's alerts load —
    // not just whichever were on the first page.
    await waitFor(() => expect(lastAlertsParams().asset_id).toBe(ASSET_ID));
  });

  it('Enter applies the typed text as the title/description search, with no asset filter', async () => {
    renderPage();
    fireEvent.change(omniInput(), { target: { value: 'disk full' } });
    fireEvent.keyDown(omniInput(), { key: 'Enter' });
    await waitFor(() => expect(lastAlertsParams().search).toBe('disk full'));
    expect(lastAlertsParams().asset_id).toBeUndefined();
  });

  it('an asset-details deep-link (?asset_id=&asset_label=) lands as the token and filters server-side', () => {
    renderPage([`/alerts?asset_id=${ASSET_ID}&asset_label=host-1`]);
    // The label from the URL renders as the token…
    expect(screen.getByText('host-1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /clear asset filter/i })).toBeInTheDocument();
    // …and the very first request is already scoped by asset_id (server-side,
    // so the header count and the list finally agree).
    expect(lastAlertsParams().asset_id).toBe(ASSET_ID);
  });

  it('the token ✕ clears the asset filter and drops asset_id from the request', async () => {
    renderPage([`/alerts?asset_id=${ASSET_ID}&asset_label=host-1`]);
    expect(lastAlertsParams().asset_id).toBe(ASSET_ID);
    fireEvent.click(screen.getByRole('button', { name: /clear asset filter/i }));
    await waitFor(() => expect(lastAlertsParams().asset_id).toBeUndefined());
    expect(screen.queryByRole('button', { name: /clear asset filter/i })).not.toBeInTheDocument();
  });

  it('an asset token coexists with an active text search (both reach the server)', async () => {
    renderPage([`/alerts?asset_id=${ASSET_ID}&asset_label=host-1`]);
    fireEvent.change(omniInput(), { target: { value: 'timeout' } });
    fireEvent.keyDown(omniInput(), { key: 'Enter' });
    await waitFor(() => {
      const p = lastAlertsParams();
      expect(p.asset_id).toBe(ASSET_ID); // token survives committing a text search
      expect(p.search).toBe('timeout');  // …and both are sent, AND-combined server-side
    });
  });
});
