// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const chartProps = vi.fn();
vi.mock('@/components/charts/DotDNAChart', () => ({
  DotDNAChart: (props: Record<string, unknown>) => {
    chartProps(props);
    return React.createElement('div', { 'data-testid': 'stub' });
  },
}));

import { StackedBarRenderer } from '../StackedBarRenderer';
import { makeStackedBarArtifact } from './fixtures';


describe('StackedBarRenderer', () => {
  it('renders the title and feeds DotDNAStackedColumn shaped data', () => {
    chartProps.mockClear();
    render(<StackedBarRenderer artifact={makeStackedBarArtifact()} />);
    expect(screen.getByText(/Assets by Category × OS Normalized/))
      .toBeInTheDocument();
    const props = chartProps.mock.calls[0][0];
    expect(props.type).toBe('DotDNAStackedColumn');
    expect(Array.isArray(props.data)).toBe(true);
    expect(props.data[0]).toMatchObject({
      label: expect.any(String),
      segments: expect.any(Array),
    });
  });

  it('passes per-category colors via segmentColors config', () => {
    chartProps.mockClear();
    const a = makeStackedBarArtifact({
      series: [{
        x_label: 'win',
        stacks: [{ label: 'server', value: 5 }],
      }],
    });
    render(<StackedBarRenderer artifact={a} />);
    const props = chartProps.mock.calls[0][0];
    expect(props.config.segmentColors).toMatchObject({
      server: '#3b82f6',
    });
  });

  it('omits unknown segment labels from segmentColors', () => {
    chartProps.mockClear();
    const a = makeStackedBarArtifact({
      series: [{
        x_label: 'cat1',
        stacks: [{ label: 'frobnicate', value: 5 }],
      }],
    });
    render(<StackedBarRenderer artifact={a} />);
    const props = chartProps.mock.calls[0][0];
    // Unknown label NOT in segmentColors map.
    expect(props.config.segmentColors).not.toHaveProperty('frobnicate');
  });
});
