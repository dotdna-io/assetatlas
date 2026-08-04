// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * "View assets in Explorer" on the alert drilldown (#907).
 *
 * The drilldown grid stops at 12 cards, so for a wider alert this link is the
 * only route to the rest. It carries one Alert search token, never the
 * asset ids: the alert's *name* when open (`Alert = "<name>"`), its *id*
 * when resolved (`Alert = <id>`) — see alertSearchToken() below for why.
 * Either way the Explorer hands that token to the server, which resolves the
 * matching assets itself. That is why an alert naming 35,136 assets (the
 * widest on the dev DB) links exactly like one naming three — and why none
 * of the URL-length cap or ~100-asset threshold the issue originally
 * proposed had to be built.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import type { Alert } from '@/api/alerts';

vi.mock('@/api/alerts', async () => {
  const actual = await vi.importActual<typeof import('@/api/alerts')>('@/api/alerts');
  return { ...actual, useAlerts: vi.fn() };
});
// A non-empty base path, because the real one is empty in dev and in jsdom —
// so a link that forgot assetUrl() would pass every assertion here while 404ing
// on deployments served under a path prefix. Mocked, the omission shows up.
vi.mock('@/basePath', () => ({
  BASE_PATH: '/assetatlas',
  assetUrl: (p: string) => '/assetatlas' + p,
}));

import { useAlerts } from '@/api/alerts';
import AlertsPage, { alertSearchToken } from '../../AlertsPage';

const ALERT_ID = '948e3f69-818b-4649-8f77-362048686475';

// Relative to now, not a literal: the page filters to the last 7 days by
// default, so a hardcoded date silently stops rendering the list once it ages
// out — which is exactly what has happened to AlertsPage.prometheus.test.tsx.
const RECENT = new Date(Date.now() - 3600_000).toISOString();

function makeAlert(over: Partial<Alert> = {}): Alert {
  return {
    id: ALERT_ID,
    type: 'security',
    severity: 'high',
    status: 'active',
    title: 'Asset With Public IP Address',
    description: 'Assets are reachable from the public internet.',
    reason: null,
    source: 'system',
    asset_count: 3179,
    assets: [{ id: 'asset-1', hostname: 'gym-versant-80', label: 'gym-versant-80' } as never],
    assets_by_category: { workstation: 3179 },
    data_sources: [],
    subject_type: 'asset',
    threshold: null,
    fingerprint: 'fp-907',
    labels: {},
    created_at: RECENT,
    updated_at: RECENT,
    resolved_at: null,
    ...over,
  } as Alert;
}

function renderWith(alert: Alert) {
  (useAlerts as never as ReturnType<typeof vi.fn>).mockReturnValue({
    data: {
      alerts: [alert],
      summary: { total: 1, by_severity: {}, by_type: {}, by_status: {} },
      timeline: [],
      pagination: { page: 1, per_page: 20, total_items: 1, total_pages: 1 },
    },
    isLoading: false,
    error: null,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AlertsPage />
      </BrowserRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText(alert.title));   // expand the drilldown
  return utils;
}

describe('alertSearchToken', () => {
  // An OPEN alert is addressed by name: readable, autocompletes, and survives
  // into a saved filter. A RESOLVED one cannot be — at most one alert per rule
  // is open at a time, so the name selects the OPEN row for that rule and would
  // answer the wrong set (or zero) for a historical card.
  it('addresses an open alert by name', () => {
    expect(
      alertSearchToken({ id: 'x', title: 'Asset With Public IP Address', status: 'active' }),
    ).toBe('Alert = "Asset With Public IP Address"');
  });

  it('addresses an acknowledged alert by name', () => {
    expect(
      alertSearchToken({ id: 'x', title: 'Data Source Stale', status: 'acknowledged' }),
    ).toBe('Alert = "Data Source Stale"');
  });

  it('addresses a resolved alert by id', () => {
    expect(
      alertSearchToken({ id: 'abc-123', title: 'Assets Disappeared', status: 'resolved' }),
    ).toBe('Alert = abc-123');
  });

  // The parser strips ONE wrapping quote pair and has no escape syntax, so the
  // quote character has to be one the title does not contain.
  it('switches quote character when the title contains a double quote', () => {
    expect(
      alertSearchToken({ id: 'x', title: 'Host "web01" flagged', status: 'active' }),
    ).toBe(`Alert = 'Host "web01" flagged'`);
  });
});

describe('AlertsPage — Explorer drilldown link', () => {
  it('links to the Explorer with an Alert token naming the alert', () => {
    renderWith(makeAlert());
    const link = screen.getByRole('link', { name: /view 3179 assets in explorer/i });
    // Prefixed via assetUrl(), as the other Explorer drilldowns are
    // (ConfidenceFlow, CategoryStrip): some deployments are served under a base path, where a
    // bare /explorer href 404s.
    expect(link).toHaveAttribute(
      'href',
      '/assetatlas/explorer?search=Alert%20%3D%20%22Asset%20With%20Public%20IP%20Address%22',
    );
  });

  it('addresses a resolved alert by id, whose name would select the open row', () => {
    renderWith(makeAlert({ status: 'resolved' }));
    const href = screen.getByRole('link', { name: /in explorer/i }).getAttribute('href') ?? '';
    expect(href).toBe(`/assetatlas/explorer?search=Alert%20%3D%20${ALERT_ID}`);
  });

  it('carries one alert token — never the asset ids it stands for', () => {
    // The design in one assertion. 3179 uuids would be ~117KB of URL; this stays
    // ~90 chars. If a future change inlines the ids, this fails.
    renderWith(makeAlert());
    const href = screen.getByRole('link', { name: /in explorer/i }).getAttribute('href') ?? '';
    expect(href).not.toContain('asset_ids');
    expect(href).not.toContain('alert_id=');    // the superseded param
    expect(href.length).toBeLessThan(200);
  });

  it('says "1 asset", not "1 assets", when the alert names one', () => {
    renderWith(makeAlert({ asset_count: 1 }));
    expect(screen.getByRole('link', { name: 'View 1 asset in Explorer' })).toBeInTheDocument();
  });

  it('offers no Explorer link for a Prometheus alert', () => {
    // Prometheus alerts are about infrastructure targets, not fleet assets;
    // the drilldown renders no asset grid for them, so a link into an
    // asset-scoped Explorer would be a dead end.
    renderWith(makeAlert({ source: 'prometheus', asset_count: 0, assets: [] }));
    expect(screen.queryByRole('link', { name: /in explorer/i })).not.toBeInTheDocument();
  });

  it('offers no Explorer link when the alert names no assets', () => {
    renderWith(makeAlert({ asset_count: 0, assets: [], assets_by_category: {} }));
    expect(screen.queryByRole('link', { name: /in explorer/i })).not.toBeInTheDocument();
  });

  it('offers no Explorer link when the alert is not about assets', () => {
    // An integration-subject alert stores data-source ids in asset_ids, so the
    // Explorer would resolve them to nothing. Observed live: the dev DB's
    // "Data Source Stale" alert names 34 ids, 0 of which are assets.
    renderWith(makeAlert({ subject_type: 'integration' }));
    expect(screen.queryByRole('link', { name: /in explorer/i })).not.toBeInTheDocument();
  });
});
