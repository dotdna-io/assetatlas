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

// Relative to "now" so this suite never bit-rots against the default 7d time-range filter
// (unlike AlertsPage.prometheus.test.tsx, whose hardcoded 2026-04-24 fixture has since aged
// out of every preset window).
const RECENT = new Date(Date.now() - 3600_000).toISOString();

const baseAlert: Alert = {
  id: 'a-1',
  type: 'operational',
  severity: 'high',
  status: 'active',
  title: 'Base alert',
  description: null,
  reason: 'because',
  source: 'system',
  asset_count: 0,
  assets: [],
  assets_by_category: null,
  data_sources: [],
  subject_type: 'asset',
  subjects: null,
  threshold: null,
  created_at: RECENT,
  updated_at: RECENT,
  resolved_at: null,
};

function renderWithAlert(alert: Alert) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  (useAlerts as any).mockReturnValue({
    data: {
      alerts: [alert],
      summary: { total: 1, by_severity: {}, by_type: {}, by_status: {} },
      timeline: [],
      pagination: { page: 1, per_page: 20, total_items: 1, total_pages: 1 },
    },
    isLoading: false,
    error: null,
  });
  render(
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AlertsPage />
      </BrowserRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText(alert.title));
}

describe('AlertsPage — subject_type drilldown branching', () => {
  it('Prometheus alerts never fall into the asset-grid branch, even though subject_type defaults to "asset"', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Redis is down',
      source: 'prometheus',
      subject_type: 'asset', // backend default for prometheus rows — the regression risk
      labels: { instance: 'redis:6379' },
    });

    // Existing Prometheus behaviour is preserved: instance label, no grid.
    expect(screen.getByText(/Instance: redis:6379/)).toBeInTheDocument();
    expect(screen.queryByText('Involved Assets')).not.toBeInTheDocument();
    // And the new subject_type branch must not sneak in a subjects pane either.
    expect(screen.queryByText('Data Sources')).not.toBeInTheDocument();
    expect(screen.queryByText('Audit Events')).not.toBeInTheDocument();
  });

  it('asset alerts still render the existing Involved Assets grid unchanged', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Asset alert',
      subject_type: 'asset',
      asset_count: 1,
      assets: [{ id: 'asset-1', hostname: 'host-1', ip: '10.0.0.1', model: null }],
    });

    expect(screen.getByText('1 assets')).toBeInTheDocument();
    expect(screen.getByText('Involved Assets')).toBeInTheDocument();
    expect(screen.getByText('host-1')).toBeInTheDocument();
  });

  it('integration alerts render the Data Sources list with a link to the integration details page', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Integration alert',
      subject_type: 'integration',
      asset_count: 1,
      subjects: [{ id: 'int-1', label: 'CrowdStrike', sublabel: 'EDR · sync failed' }],
    });

    expect(screen.getByText('1 data sources')).toBeInTheDocument();
    expect(screen.getByText('Data Sources')).toBeInTheDocument();
    const link = screen.getByText('CrowdStrike').closest('a');
    expect(link).toHaveAttribute('href', '/integration-details?id=int-1');
  });

  it('audit_event alerts render the Audit Events list as non-clickable rows', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Audit event alert',
      subject_type: 'audit_event',
      asset_count: 1,
      subjects: [{ id: 'evt-1', label: 'admin@example.com', sublabel: 'role changed' }],
    });

    expect(screen.getByText('1 events')).toBeInTheDocument();
    expect(screen.getByText('Audit Events')).toBeInTheDocument();
    expect(screen.getByText('admin@example.com').closest('a')).toBeNull();
  });

  it('summary-only alerts (e.g. pipeline_run) render no right pane at all', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Pipeline alert',
      subject_type: 'pipeline_run',
      asset_count: 0,
      subjects: null,
    });

    expect(screen.getByText('because')).toBeInTheDocument(); // left pane still renders
    expect(screen.queryByText('Involved Assets')).not.toBeInTheDocument();
    expect(screen.queryByText('Data Sources')).not.toBeInTheDocument();
    expect(screen.queryByText('Audit Events')).not.toBeInTheDocument();
  });

  it('metric-subject anomaly alert renders no Involved Assets grid', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Metric alert',
      subject_type: 'metric',
      asset_count: 0,
      subjects: null,
      evidence: { observed: 500, sample: [{ id: 'a1', label: 'web-01' }] },
    });

    expect(screen.queryByText('Involved Assets')).not.toBeInTheDocument();
  });
});
