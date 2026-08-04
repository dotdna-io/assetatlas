// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// Stub the vanilla-JS-wrapping DotDNAChart so we test ChartArtifact in
// isolation — the wrapper itself needs DarkModeProvider and touches
// window globals that have no value in a unit test. Record the props it
// receives so we can assert the data we hand down is well-formed.
const chartProps = vi.fn();
vi.mock('@/components/charts/DotDNAChart', () => ({
  DotDNAChart: (props: Record<string, unknown>) => {
    chartProps(props);
    return React.createElement('div', { 'data-testid': 'dotdna-chart-stub' });
  },
}));

vi.mock('../StackedBarRenderer', () => ({
  StackedBarRenderer: () =>
    React.createElement('div', { 'data-testid': 'stacked-renderer' }),
}));
vi.mock('../TableRenderer', () => ({
  TableRenderer: () =>
    React.createElement('div', { 'data-testid': 'table-renderer' }),
}));

import { ChartArtifact } from '../ChartArtifact';
import type { DonutArtifact } from '../chartArtifactTypes';
import { makeDonutArtifact, makeStackedBarArtifact, makeTableArtifact } from './fixtures';


const donut = () => makeDonutArtifact({
  series: [
    { label: 'server', value: 6 },
    { label: 'workstation', value: 4 },
  ],
});


describe('ChartArtifact', () => {
  it('renders the chart title and a data-table fallback', () => {
    render(<ChartArtifact artifact={donut()} />);

    expect(screen.getByTestId('dotdna-chart-stub')).toBeInTheDocument();
    expect(screen.getByText('Assets by Category')).toBeInTheDocument();
    // <details> is collapsed initially; the summary is still in the DOM.
    const summary = screen.getByText(/Data \(10 total\)/);
    expect(summary).toBeInTheDocument();

    // Reveal the fallback table and confirm it carries every slice.
    fireEvent.click(summary);
    expect(screen.getByText('server')).toBeInTheDocument();
    expect(screen.getByText('workstation')).toBeInTheDocument();
    expect(screen.getByText('60.0%')).toBeInTheDocument();
    expect(screen.getByText('40.0%')).toBeInTheDocument();
  });

  it('uses row_field as the first column header when provided', () => {
    render(<ChartArtifact artifact={donut()} />);
    fireEvent.click(screen.getByText(/Data \(10 total\)/));
    // <th scope="col"> matches by accessible name.
    expect(
      screen.getByRole('columnheader', { name: 'category' }),
    ).toBeInTheDocument();
  });

  it('falls back to "Value" when row_field is absent', () => {
    const a = donut();
    delete (a.meta as any).row_field;
    render(<ChartArtifact artifact={a} />);
    fireEvent.click(screen.getByText(/Data \(10 total\)/));
    expect(
      screen.getByRole('columnheader', { name: 'Value' }),
    ).toBeInTheDocument();
  });

  it('passes DotDNAChart a percentage-shaped data array', () => {
    chartProps.mockClear();
    render(<ChartArtifact artifact={donut()} />);
    expect(chartProps).toHaveBeenCalledTimes(1);
    const lastProps = chartProps.mock.calls[0][0];
    expect(lastProps.type).toBe('DotDNAAnimatedDonutModern');
    expect(lastProps.data).toEqual([
      { name: 'server', value: 60, count: 6,
        gradient: expect.any(Array) },
      { name: 'workstation', value: 40, count: 4,
        gradient: expect.any(Array) },
    ]);
  });

  it('labels "Other" as a row in the data table', () => {
    const a: DonutArtifact = {
      ...donut(),
      series: [
        { label: 'server', value: 6 },
        { label: 'Other', value: 4 },
      ],
      meta: { ...donut().meta, other_count: 3 },
    };
    render(<ChartArtifact artifact={a} />);
    fireEvent.click(screen.getByText(/Data \(10 total\)/));
    expect(screen.getByText('Other')).toBeInTheDocument();
  });
});


describe('ChartArtifact dispatch', () => {
  it('routes a stacked_bar artifact to StackedBarRenderer', () => {
    render(<ChartArtifact artifact={makeStackedBarArtifact()} />);
    expect(screen.getByTestId('stacked-renderer')).toBeInTheDocument();
  });

  it('routes a table artifact to TableRenderer', () => {
    render(<ChartArtifact artifact={makeTableArtifact()} />);
    expect(screen.getByTestId('table-renderer')).toBeInTheDocument();
  });

  it('routes a donut artifact to the inline donut path (regression)', () => {
    render(<ChartArtifact artifact={makeDonutArtifact()} />);
    expect(screen.getByTestId('dotdna-chart-stub')).toBeInTheDocument();
  });
});


describe('ChartArtifact donut · category coloring', () => {
  it('uses the shared category color when a slice label is a known category', () => {
    chartProps.mockClear();
    const a = makeDonutArtifact({
      series: [
        { label: 'server',      value: 6 },
        { label: 'workstation', value: 4 },
      ],
    });
    render(<ChartArtifact artifact={a} />);
    const data = chartProps.mock.calls[0][0].data;
    // server gradient base should match getCategoryColor('server') = #3b82f6
    const serverDatum = data.find((d: any) => d.name === 'server');
    expect(serverDatum.gradient[0]).toBe('#3b82f6');
    // workstation gradient base should match #8b5cf6
    const wsDatum = data.find((d: any) => d.name === 'workstation');
    expect(wsDatum.gradient[0]).toBe('#8b5cf6');
  });

  it('falls back to the position palette for unknown labels', () => {
    chartProps.mockClear();
    const a = makeDonutArtifact({
      series: [
        { label: 'foo', value: 6 },
        { label: 'bar', value: 4 },
      ],
    });
    render(<ChartArtifact artifact={a} />);
    const data = chartProps.mock.calls[0][0].data;
    // First palette gradient is ['#3B82F6', '#2563EB'] per chartArtifactTypes
    expect(data[0].gradient).toEqual(['#3B82F6', '#2563EB']);
    expect(data[1].gradient).toEqual(['#10B981', '#059669']);
  });

  it('keeps the muted "Other" gradient', () => {
    chartProps.mockClear();
    const a = makeDonutArtifact({
      series: [
        { label: 'server', value: 6 },
        { label: 'Other',  value: 4 },
      ],
      meta: { other_count: 3 },
    });
    render(<ChartArtifact artifact={a} />);
    const data = chartProps.mock.calls[0][0].data;
    const other = data.find((d: any) => d.name === 'Other');
    expect(other.gradient).toEqual(['#6B7280', '#4B5563']);
  });
});
