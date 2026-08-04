// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import MonitoringPage from '../MonitoringPage';
import { useMonitoringAvailable } from '../useMonitoringAvailable';

// The page gates the dashboards on whether the Prometheus proxy is enabled
// server-side. Mock the availability hook so these tests drive the enabled path
// directly (the hook's own health wiring is covered in useMonitoringAvailable.test).
vi.mock('../useMonitoringAvailable', () => ({
  useMonitoringAvailable: vi.fn(() => true),
}));

const mockAvailable = vi.mocked(useMonitoringAvailable);

function renderAt(path: string, { seedHealth = true }: { seedHealth?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // MonitoringPage reads useHealth() to tell "monitoring off" from "health not
  // loaded yet". Seed the shared ['health'] query so it's resolved by default
  // (as it is in the running app, where the sidebar probe warms it); pass
  // seedHealth:false to exercise the still-loading path.
  if (seedHealth) {
    qc.setQueryData(['health'], {
      status: 'healthy',
      application: { features: { monitoring: false } },
    });
  }
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/monitoring" element={<MonitoringPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('MonitoringPage', () => {
  beforeEach(() => {
    mockAvailable.mockReturnValue(true);
  });

  it('renders all 6 tabs and defaults to System', () => {
    renderAt('/monitoring');
    expect(screen.getByRole('button', { name: 'System' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Database' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Redis' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pipeline' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Host' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Containers' })).toBeInTheDocument();
    // Default tab is system → SystemDashboard renders
    expect(screen.getByText('Request rate')).toBeInTheDocument();
  });

  it('reads tab from URL query', () => {
    renderAt('/monitoring?tab=database');
    expect(screen.getByText('Query latency p50/p95/p99')).toBeInTheDocument();
  });

  it('clicking a different tab updates the active dashboard', () => {
    renderAt('/monitoring');
    fireEvent.click(screen.getByRole('button', { name: 'Redis' }));
    expect(screen.getByText('Hit rate')).toBeInTheDocument();
  });

  it('reads range preset from URL query and marks active button', () => {
    renderAt('/monitoring?range=24h');
    const btn = screen.getByRole('button', { name: 'Last 24h' });
    // The active class name will contain 'presetActive' or similar — just check className is not the default
    expect(btn.className).toMatch(/presetActive|active/i);
  });

  it('shows a "not enabled" notice — and no dashboards — when monitoring is unavailable', () => {
    // MONITORING_ENABLED=false server-side → the /api/monitoring proxy 404s.
    // The page must render a notice, and crucially render NO dashboards/tabs so
    // nothing fires the doomed /api/monitoring/* requests.
    mockAvailable.mockReturnValue(false);
    renderAt('/monitoring');
    expect(screen.getByText('Monitoring is not enabled')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'System' })).not.toBeInTheDocument();
    expect(screen.queryByText('Request rate')).not.toBeInTheDocument();
  });

  it('renders neither dashboards nor the notice while health is still loading', () => {
    // Before /api/health resolves we can't tell "off" from "loading" — the page
    // must NOT claim "not enabled" (it would be wrong on an enabled deployment
    // during a cold fetch) and must NOT render dashboards (they'd fire 404s).
    mockAvailable.mockReturnValue(false);
    renderAt('/monitoring', { seedHealth: false });
    expect(screen.queryByText('Monitoring is not enabled')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'System' })).not.toBeInTheDocument();
  });
});
