// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import type { Alert } from '@/api/alerts';

vi.mock('@/api/alerts', async () => {
  const actual = await vi.importActual<typeof import('@/api/alerts')>('@/api/alerts');
  return { ...actual, useAlerts: vi.fn(), useAlertHistory: vi.fn() };
});

import { useAlerts, useAlertHistory } from '@/api/alerts';
import AlertsPage from '../../AlertsPage';

const NOW = new Date().toISOString();

function makeAssets(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `asset-${i}`, hostname: `host-${i}`, label: `host-${i}`,
  })) as never[];
}

function alertWithAssets(over: Partial<Alert>): Alert {
  return {
    id: 'a', type: 'security', severity: 'high', status: 'active',
    title: 'Coverage alert', description: 'More than 5% uncovered.',
    reason: 'Current value 37.3% is above the 5% threshold', source: 'system',
    asset_count: 8, assets: makeAssets(8), assets_by_category: {}, data_sources: [],
    subject_type: 'asset', threshold: null, created_at: NOW, updated_at: NOW,
    resolved_at: null, last_pipeline_run_id: 'run-9', ...over,
  };
}

function mockAlerts(alerts: Alert[]) {
  (useAlerts as any).mockReturnValue({
    data: {
      alerts,
      summary: { total: alerts.length, by_severity: {}, by_type: {}, by_status: {} },
      timeline: [],
      latest_pipeline_run_id: 'run-9',
      pagination: { page: 1, per_page: 20, total_items: alerts.length, total_pages: 1 },
    },
    isLoading: false,
    error: null,
  });
}

function renderExpanded(alert: Alert) {
  mockAlerts([alert]);
  (useAlertHistory as any).mockReturnValue({
    data: { alert_id: 'a', history: [] }, isLoading: false, error: null,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AlertsPage />
      </BrowserRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText(alert.title));
  return utils;
}

describe('AlertsPage — expanded alert layout', () => {
  beforeEach(() => vi.clearAllMocks());

  it('puts Description in the left pane and Reason with the assets in the right pane', () => {
    const { container } = renderExpanded(alertWithAssets({}));

    const left = container.querySelector('[class*="expandedLeft"]')!;
    const right = container.querySelector('[class*="expandedRight"]')!;
    expect(left.textContent).toContain('Description');
    expect(left.textContent).not.toContain('Reason');
    expect(right.textContent).toContain('Reason');
    expect(right.textContent).toContain('Involved Assets');
  });

  it('caps the asset grid at 6 entries and counts the rest in "+N more assets"', () => {
    renderExpanded(alertWithAssets({ asset_count: 8, assets: makeAssets(8) }));

    for (let i = 0; i < 6; i++) {
      expect(screen.getByText(`host-${i}`)).toBeInTheDocument();
    }
    expect(screen.queryByText('host-6')).not.toBeInTheDocument();
    expect(screen.getByText('+2 more assets')).toBeInTheDocument();
    expect(screen.getByText(/view 8 assets in explorer/i)).toBeInTheDocument();
  });

  it('shows no "+N more" line when six or fewer assets are involved', () => {
    renderExpanded(alertWithAssets({ asset_count: 6, assets: makeAssets(6) }));
    expect(screen.queryByText(/more assets/)).not.toBeInTheDocument();
  });
});
