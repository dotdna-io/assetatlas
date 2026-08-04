// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/api/monitoring', async () => {
  const actual = await vi.importActual<typeof import('@/api/monitoring')>('@/api/monitoring');
  return {
    ...actual,
    useInstantQuery: vi.fn(() => ({
      data: { resultType: 'vector', result: [{ metric: {}, value: [123, '0'] }] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })),
    useRangeQuery: vi.fn(() => ({
      data: { resultType: 'matrix', result: [] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })),
  };
});

vi.mock('../tiles/LineChartTile', () => ({
  LineChartTile: (props: { title?: string }) => React.createElement('div', { 'data-testid': 'line-chart-tile' }, props.title),
}));

vi.mock('../tiles/TableTile', () => ({
  TableTile: (props: { title?: string }) => React.createElement('div', { 'data-testid': 'table-tile' }, props.title),
}));

import { SystemDashboard } from '../dashboards/SystemDashboard';

function renderDashboard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SystemDashboard start="0" end="100" step="15s" rateWindow="1m" refetchInterval={false} />
    </QueryClientProvider>,
  );
}

describe('SystemDashboard', () => {
  it('renders 5 tiles', () => {
    renderDashboard();
    expect(screen.getByText('Request rate')).toBeInTheDocument();
    expect(screen.getByText('Error rate (4xx vs 5xx)')).toBeInTheDocument();
    expect(screen.getByText('Latency p50/p95/p99')).toBeInTheDocument();
    expect(screen.getByText('Top endpoints by p95 latency')).toBeInTheDocument();
    expect(screen.getByText('Top endpoints by 5xx rate')).toBeInTheDocument();
  });
});
