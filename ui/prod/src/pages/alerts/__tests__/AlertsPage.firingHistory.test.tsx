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

function customAlert(over: Partial<Alert>): Alert {
  return {
    id: 'a', type: 'operational', severity: 'high', status: 'active',
    title: 'Alert', description: null, reason: null, source: 'custom',
    asset_count: 3, assets: [], assets_by_category: {}, data_sources: [],
    subject_type: 'asset', threshold: null, created_at: NOW, updated_at: NOW,
    resolved_at: null, ...over,
  };
}

function mockAlerts(alerts: Alert[], latest_pipeline_run_id: string | null) {
  (useAlerts as any).mockReturnValue({
    data: {
      alerts,
      summary: { total: alerts.length, by_severity: {}, by_type: {}, by_status: {} },
      timeline: [],
      latest_pipeline_run_id,
      pagination: { page: 1, per_page: 20, total_items: alerts.length, total_pages: 1 },
    },
    isLoading: false,
    error: null,
  });
}

function mockHistory() {
  (useAlertHistory as any).mockReturnValue({
    data: {
      alert_id: 'a',
      history: [
        { pipeline_run_id: 'r1', status: 'active', asset_count: 4, severity: 'high',
          created_at: NOW, started_at: NOW, completed_at: NOW },
        { pipeline_run_id: 'r2', status: 'active', asset_count: 6, severity: 'high',
          created_at: NOW, started_at: NOW, completed_at: NOW },
      ],
    },
    isLoading: false,
    error: null,
  });
}

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

describe('AlertsPage — assets-over-time chart in the drilldown', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the chart when a pipeline alert with assets is expanded', () => {
    mockAlerts([customAlert({ title: 'Pipeline alert', last_pipeline_run_id: 'run-9' })], 'run-9');
    mockHistory();
    renderPage();

    // Collapsed: no chart yet (the drilldown mounts it lazily).
    expect(screen.queryByTestId('alert-history-point')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Pipeline alert'));
    expect(screen.getAllByTestId('alert-history-point').length).toBe(2);
  });

  it('does not render a chart for a non-pipeline alert (no run id)', () => {
    mockAlerts(
      [customAlert({ title: 'Update available', source: 'system', last_pipeline_run_id: null })],
      'run-9',
    );
    (useAlertHistory as any).mockReturnValue({ data: undefined, isLoading: false, error: null });
    renderPage();

    fireEvent.click(screen.getByText('Update available'));
    expect(screen.queryByTestId('alert-history-point')).not.toBeInTheDocument();
    expect(useAlertHistory as any).not.toHaveBeenCalled();
  });

  it('does not render a chart for an alert without assets', () => {
    mockAlerts(
      [customAlert({
        title: 'Assetless alert', last_pipeline_run_id: 'run-9',
        asset_count: 0, assets: [], description: 'd',
      })],
      'run-9',
    );
    (useAlertHistory as any).mockReturnValue({ data: undefined, isLoading: false, error: null });
    renderPage();

    fireEvent.click(screen.getByText('Assetless alert'));
    expect(screen.queryByTestId('alert-history-point')).not.toBeInTheDocument();
  });

  it('does not render a chart for a non-asset subject (integration alert)', () => {
    mockAlerts(
      [customAlert({
        title: 'Source stale', last_pipeline_run_id: 'run-9',
        subject_type: 'integration', asset_count: 2,
        subjects: [{ id: 'i1', label: 'SCCM' }] as never,
      })],
      'run-9',
    );
    (useAlertHistory as any).mockReturnValue({ data: undefined, isLoading: false, error: null });
    renderPage();

    fireEvent.click(screen.getByText('Source stale'));
    expect(screen.queryByTestId('alert-history-point')).not.toBeInTheDocument();
  });
});
