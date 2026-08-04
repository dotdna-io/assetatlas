// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import type { Alert } from '@/api/alerts';

vi.mock('@/api/alerts', async () => {
  const actual = await vi.importActual<typeof import('@/api/alerts')>('@/api/alerts');
  return {
    ...actual,
    useAlerts: vi.fn(),
  };
});

import { useAlerts } from '@/api/alerts';
import AlertsPage from '../../AlertsPage';

// Relative timestamp: the page's default 30d window filters client-side, so a
// hardcoded date silently ages out of range and empties the rendered list.
const NOW = new Date().toISOString();

const prometheusAlert: Alert = {
  id: 'a-prom-1',
  type: 'operational',
  severity: 'critical',
  status: 'active',
  title: 'Redis is down',
  description: 'redis-exporter cannot reach Redis for 2+ minutes.',
  reason: null,
  source: 'prometheus',
  asset_count: 0,
  assets: [],
  assets_by_category: {},
  data_sources: [],
  subject_type: 'asset',
  threshold: null,
  fingerprint: 'fp-1',
  labels: { alertname: 'redis-down', severity: 'critical', instance: 'redis:6379' },
  created_at: NOW,
  updated_at: NOW,
  resolved_at: null,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AlertsPage />
      </BrowserRouter>
    </QueryClientProvider>,
  );
}

describe('AlertsPage — source=prometheus rendering', () => {
  it('shows the Prometheus badge and Instance chip; hides asset count', () => {
    (useAlerts as any).mockReturnValue({
      data: {
        alerts: [prometheusAlert],
        summary: { total: 1, by_severity: {}, by_type: {}, by_status: {} },
        timeline: [],
        pagination: { page: 1, per_page: 20, total_items: 1, total_pages: 1 },
      },
      isLoading: false,
      error: null,
    });
    renderPage();

    expect(screen.getByText(/Prometheus/i)).toBeInTheDocument();
    expect(screen.getByText(/Instance: redis:6379/i)).toBeInTheDocument();
    expect(screen.queryByText(/0 assets/i)).not.toBeInTheDocument();
  });

  it('does not render the asset list when a Prometheus alert is expanded', () => {
    (useAlerts as any).mockReturnValue({
      data: {
        alerts: [prometheusAlert],
        summary: { total: 1, by_severity: {}, by_type: {}, by_status: {} },
        timeline: [],
        pagination: { page: 1, per_page: 20, total_items: 1, total_pages: 1 },
      },
      isLoading: false,
      error: null,
    });
    renderPage();

    fireEvent.click(screen.getByText('Redis is down'));

    expect(screen.queryByTestId('alert-asset-list')).not.toBeInTheDocument();
    expect(screen.queryAllByRole('img', { name: /asset/i })).toHaveLength(0);
  });
});
