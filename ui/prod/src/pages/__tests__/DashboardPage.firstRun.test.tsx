// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import DashboardPage from '../DashboardPage';

/** Swapped per test — stands in for the GET /integrations query result. */
let integrationsResult: { isSuccess: boolean; data?: { count: number } };

vi.mock('@/api/dashboard', () => ({
  useDashboardSummary: () => ({
    isPending: false,
    data: { totalAssets: 4210, lastUpdated: null, categoryBreakdown: {} },
  }),
  useDashboardAlerts: () => ({ isPending: false, data: { alerts: [] } }),
  useDataSources: () => ({ isPending: false, data: { sources: [] } }),
  useDashboardConfidence: () => ({ isPending: false, data: undefined }),
  useDashboardAssetTimeline: () => ({ data: undefined }),
  useDashboardCoverageTrend: () => ({ data: undefined }),
}));

vi.mock('@/api/integrations', () => ({
  useIntegrationGroups: () => ({ isPending: false, data: { groups: [] } }),
  useIntegrationGroupMapping: () => ({ isPending: false, data: { mapping: {} } }),
  useIntegrationGroup: () => ({ isPending: false, data: undefined }),
  useIntegrations: () => integrationsResult,
}));

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

const firstRunHeading = /Your inventory starts with one connection/;

describe('DashboardPage first-run state', () => {
  beforeEach(() => vi.clearAllMocks());

  it('replaces the dashboard with first-run guidance when nothing is configured', () => {
    integrationsResult = { isSuccess: true, data: { count: 0 } };
    renderDashboard();

    expect(screen.getByText(firstRunHeading)).toBeInTheDocument();
    // Replaces rather than sits above: the zeroed dashboard must be gone.
    expect(screen.queryByText('Total Assets')).not.toBeInTheDocument();
  });

  it('shows the dashboard once any integration exists', () => {
    integrationsResult = { isSuccess: true, data: { count: 1 } };
    renderDashboard();

    expect(screen.queryByText(firstRunHeading)).not.toBeInTheDocument();
    expect(screen.getByText('Total Assets')).toBeInTheDocument();
  });

  // The guard that matters: on a failed request `data` is undefined, and
  // reading that as a count of zero would tell an established install to go
  // and connect its first data source.
  it('does not claim first run when the integrations request failed', () => {
    integrationsResult = { isSuccess: false, data: undefined };
    renderDashboard();

    expect(screen.queryByText(firstRunHeading)).not.toBeInTheDocument();
    expect(screen.getByText('Total Assets')).toBeInTheDocument();
  });
});
