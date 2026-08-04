// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

import { TrendCard } from '../TrendCard';

vi.mock('@/api/dashboard', () => ({
  useDashboardAssetTimeline: () => ({
    data: { range: '30d', granularity: 'daily', data: [{ label: 'Jun 01', added: 1, removed: 0 }] },
  }),
  useDashboardCoverageTrend: () => ({
    data: {
      metric: 'srv_edr', range: '30d', granularity: 'daily',
      data: [{ label: 'Jun 01', added: 680, removed: 7400 }],
    },
  }),
}));

vi.mock('@/components/charts/ButterflyChart', () => ({
  ButterflyChart: (props: { data: unknown; scaleMode?: string; topLabel?: string }) =>
    React.createElement('div', {
      'data-testid': 'butterfly',
      'data-scale': props.scaleMode,
      'data-toplabel': props.topLabel,
    }, JSON.stringify(props.data)),
}));

describe('TrendCard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the real coverage series with shared scale for a posture metric', () => {
    render(<TrendCard />);

    // switch the metric dropdown to the servers-EDR coverage metric
    fireEvent.change(screen.getByLabelText('Trend metric'), { target: { value: 'srv_edr' } });

    const chart = screen.getByTestId('butterfly');
    expect(chart.getAttribute('data-scale')).toBe('shared');
    expect(chart.getAttribute('data-toplabel')).toBe('With EDR');
    expect(chart.textContent).toContain('680');
    expect(chart.textContent).toContain('7400');
  });

  it('uses split scale for the real added_removed metric', () => {
    render(<TrendCard />);
    const chart = screen.getByTestId('butterfly');
    expect(chart.getAttribute('data-scale')).toBe('split');
  });

  it('shows a 3-series legend for added_removed and a 2-series green/red legend for posture metrics', () => {
    render(<TrendCard />);

    // added_removed is the default metric → New / Returned / Departed
    let legend = screen.getByTestId('butterfly-legend');
    expect(legend).toHaveTextContent('New');
    expect(legend).toHaveTextContent('Returned');
    expect(legend).toHaveTextContent('Departed');

    // posture metric → 2-item legend using the metric's own labels, no Returned
    fireEvent.change(screen.getByLabelText('Trend metric'), { target: { value: 'srv_edr' } });
    legend = screen.getByTestId('butterfly-legend');
    expect(legend).toHaveTextContent('With EDR');
    expect(legend).toHaveTextContent('No EDR');
    expect(legend).not.toHaveTextContent('Returned');
  });
});
