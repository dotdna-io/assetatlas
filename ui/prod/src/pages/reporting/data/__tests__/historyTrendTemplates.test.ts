// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { generateChartQuery } from '../chartQuestions';

describe('historical templates emit history_trend', () => {
  it('Line Chart count over time, no timeField', () => {
    const q = generateChartQuery('DotDNALineChart', { metric: 'count', timeRange: '30d', interval: 'day' });
    expect(q!.queryType).toBe('history_trend');
    expect(q!.select.timeField).toBeUndefined();
    expect(q!.options.interval).toBe('day');
    expect(q!.options.last ?? q!.options.timeRange).toBe('30d');
  });
  it('Line Chart percentage sets showPercentage + comparisonFilters', () => {
    const q = generateChartQuery('DotDNALineChart', { metric: 'percentage', timeRange: '7d', interval: 'day', comparisonFilters: [] });
    expect(q!.queryType).toBe('history_trend');
    expect(q!.options.showPercentage).toBe(true);
  });
  it('Metric Spark emits single-series history_trend', () => {
    const q = generateChartQuery('DotDNAMetricCard', { timeRange: '30d', interval: 'day' });
    expect(q!.queryType).toBe('history_trend');
    expect(q!.select.dimension).toBeUndefined();
    expect(q!.select.timeField).toBeUndefined();
  });
});
