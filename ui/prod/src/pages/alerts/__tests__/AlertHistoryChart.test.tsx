// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/api/alerts', async () => {
  const actual = await vi.importActual<typeof import('@/api/alerts')>('@/api/alerts');
  return { ...actual, useAlertHistory: vi.fn() };
});

import { useAlertHistory } from '@/api/alerts';
import type { AlertHistoryPoint } from '@/api/alerts';
import { AlertHistoryChart } from '../AlertHistoryChart';

function point(over: Partial<AlertHistoryPoint>): AlertHistoryPoint {
  return {
    pipeline_run_id: 'r',
    status: 'active',
    asset_count: 5,
    severity: 'high',
    created_at: '2026-07-18T00:00:00Z',
    started_at: '2026-07-18T00:00:00Z',
    completed_at: '2026-07-18T00:05:00Z',
    ...over,
  };
}

function mockHistory(history: AlertHistoryPoint[] | null, isLoading = false) {
  (useAlertHistory as any).mockReturnValue({
    data: history === null ? undefined : { alert_id: 'a1', history },
    isLoading,
    error: null,
  });
}

describe('AlertHistoryChart — involved assets over time', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders a line with one hover point per history run', () => {
    mockHistory([
      point({ pipeline_run_id: 'r1', asset_count: 4 }),
      point({ pipeline_run_id: 'r2', asset_count: 6 }),
      point({ pipeline_run_id: 'r3', asset_count: 2 }),
    ]);
    render(<AlertHistoryChart alertId="a1" />);
    expect(screen.getByTestId('alert-history-line')).toBeInTheDocument();
    expect(screen.getAllByTestId('alert-history-point')).toHaveLength(3);
  });

  it('drops the old run-state chrome: no Firing/Resolved legend, no "Assets covered" title', () => {
    mockHistory([point({ pipeline_run_id: 'r1' })]);
    render(<AlertHistoryChart alertId="a1" />);
    expect(screen.getByText('Assets over time')).toBeInTheDocument();
    expect(screen.queryByText(/firing/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/resolved/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/assets covered/i)).not.toBeInTheDocument();
  });

  it('shows the window edges as date labels (oldest run left, newest right)', () => {
    mockHistory([
      point({ pipeline_run_id: 'r1', started_at: '2026-04-21T08:00:00Z' }),
      point({ pipeline_run_id: 'r2', started_at: '2026-07-18T08:00:00Z' }),
    ]);
    render(<AlertHistoryChart alertId="a1" />);
    const dates = screen.getByTestId('alert-history-dates');
    expect(dates.textContent).toContain(new Date('2026-04-21T08:00:00Z').toLocaleDateString());
    expect(dates.textContent).toContain(new Date('2026-07-18T08:00:00Z').toLocaleDateString());
  });

  it('renders a graceful empty state when there is no history', () => {
    mockHistory([]);
    render(<AlertHistoryChart alertId="a1" />);
    expect(screen.queryAllByTestId('alert-history-point')).toHaveLength(0);
    expect(screen.getByText(/no history yet/i)).toBeInTheDocument();
  });

  it('shows a distinct error state (not the empty state) when the request fails', () => {
    (useAlertHistory as any).mockReturnValue({
      data: undefined, isLoading: false, error: new Error('boom'),
    });
    render(<AlertHistoryChart alertId="a1" />);
    expect(screen.queryAllByTestId('alert-history-point')).toHaveLength(0);
    expect(screen.getByText(/couldn.t load history/i)).toBeInTheDocument();
    expect(screen.queryByText(/no history yet/i)).not.toBeInTheDocument();
  });
});
