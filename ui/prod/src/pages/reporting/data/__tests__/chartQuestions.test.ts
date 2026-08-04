// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import { generateChartQuery } from '../chartQuestions';

/**
 * Regression net for #493: assert generateChartQuery emits the exact
 * select shape the backend (ChartQueryRequest) expects for each chart type.
 *
 * The backend Pydantic equivalent lives at
 * tests/unit/blueprints/test_charts_wizard_payloads.py — that test runs the
 * post-transform wire payload through the schema. This UI-side test guards
 * the pre-transform shape so regressions surface in the wizard layer too.
 */

describe('generateChartQuery — metric template (#493)', () => {
  it('emits select.metric as a MetricSpec object for percentage gauges', () => {
    const q = generateChartQuery('DotDNACircleGauge', {
      metricType: 'percentage',
    });

    expect(q).not.toBeNull();
    expect(q!.select).toEqual({ metric: { agg: 'COUNT' } });
    expect(q!.queryType).toBe('metric');
    expect(q!.options.metricType).toBe('percentage');
    expect(q!.options.showPercentage).toBe(true);
  });

  it('emits select.metric as a MetricSpec object for filtered counts', () => {
    const q = generateChartQuery('DotDNAUltraNumber', {
      metricType: 'filtered',
    });

    expect(q).not.toBeNull();
    expect(q!.select.metric).toEqual({ agg: 'COUNT' });
    expect(q!.select.dimension).toBeUndefined();
  });

  it('keeps a MetricSpec and adds select.dimension for distinct counts', () => {
    const q = generateChartQuery('DotDNAUltraNumber', {
      metricType: 'distinct',
      distinctField: 'static:Category',
    });

    expect(q).not.toBeNull();
    expect(q!.select.metric).toEqual({ agg: 'COUNT' });
    expect(q!.select.dimension).toBe('static:Category');
    expect(q!.options.metricType).toBe('distinct');
  });

  it('covers every metric chart class with the fixed shape', () => {
    // Metric Spark (DotDNAMetricCard) & Metric Bar (DotDNAMetricBar) moved to
    // the metricTrend template (#946) and are covered by their own suite below.
    const metricCharts = [
      'DotDNACircleGauge',
      'DotDNAElegantGauge',
      'DotDNALinearGauge',
      'DotDNAUltraNumber',
    ];
    for (const className of metricCharts) {
      const q = generateChartQuery(className, { metricType: 'percentage' });
      expect(q, `chart ${className} should generate a query`).not.toBeNull();
      expect(
        q!.select.metric,
        `chart ${className} should emit a MetricSpec object`,
      ).toEqual({ agg: 'COUNT' });
    }
  });
});

describe('generateChartQuery — trend template (#493)', () => {
  it('emits history_trend, no timeField (#497/#946)', () => {
    const q = generateChartQuery('DotDNALineChart', {
      metric: 'count',
      timeRange: '30d',
      interval: 'day',
    });

    expect(q).not.toBeNull();
    expect(q!.from).toEqual({ table: 'assets' });
    expect(q!.queryType).toBe('history_trend');
    // Snapshot history is bucketed server-side by archived_at — no timeField.
    expect(q!.select.timeField).toBeUndefined();
  });

  it('sets options.showPercentage and forwards comparisonFilters', () => {
    const q = generateChartQuery('DotDNALineChart', {
      metric: 'percentage',
      timeRange: '30d',
      interval: 'day',
      comparisonFilters: [
        { field_id: { kind: 'static', name: 'Category' }, operator: 'equals', value: 'Workstation' },
      ],
    });

    expect(q).not.toBeNull();
    expect(q!.queryType).toBe('history_trend');
    expect(q!.options.showPercentage).toBe(true);
    expect(q!.comparisonFilters).toHaveLength(1);
  });

  it('adds select.dimension when the user picks a split-by field', () => {
    const q = generateChartQuery('DotDNALineChart', {
      metric: 'count',
      timeRange: '30d',
      interval: 'day',
      dimension: 'static:Category',
    });

    expect(q!.select.timeField).toBeUndefined();
    expect(q!.select.dimension).toBe('static:Category');
  });
});

/**
 * #946: Metric Spark (DotDNAMetricCard) & Metric Bar (DotDNAMetricBar) must
 * emit a time-series (trend) query, not the single-value `metric` query — that
 * is the only way their sparkline / mini-bars + stats receive a series. The
 * plain Number Chart (DotDNAUltraNumber) must stay on the single-value query.
 */
describe('generateChartQuery — metricTrend template (#946)', () => {
  const trendMetricCharts = ['DotDNAMetricCard', 'DotDNAMetricBar'];

  it.each(trendMetricCharts)(
    '%s emits a time-series history_trend query, not a metric query',
    (className) => {
      const q = generateChartQuery(className, {
        timeRange: '30d',
        interval: 'day',
      });

      expect(q).not.toBeNull();
      // The whole point of #946: a time-series query, never 'metric'. #497
      // repoints it from the live 'trend' type to snapshot-history 'history_trend'.
      expect(q!.queryType).toBe('history_trend');
      expect(q!.queryType).not.toBe('metric');
      expect(q!.from).toEqual({ table: 'assets' });
      expect(q!.select.timeField).toBeUndefined();
      // Single series: no split-by dimension, and no single-value MetricSpec.
      expect(q!.select.dimension).toBeUndefined();
      expect(q!.select.metric).toBeUndefined();
      expect(q!.options.interval).toBe('day');
      expect(q!.options.timeRange).toBe('30d');
    },
  );

  it('drops timeRange for the "all time" option so the backend uses no window', () => {
    const q = generateChartQuery('DotDNAMetricCard', {
      timeRange: 'all',
      interval: 'month',
    });

    expect(q!.options.timeRange).toBeUndefined();
    expect(q!.options.interval).toBe('month');
  });

  it('forwards asset filters', () => {
    const q = generateChartQuery('DotDNAMetricBar', {
      timeRange: '7d',
      interval: 'day',
      filters: [
        {
          field_id: { kind: 'static', name: 'Category' },
          operator: 'equals',
          value: 'Workstation',
        },
      ],
    });

    expect(q!.filters).toHaveLength(1);
  });

  it('leaves the plain Number Chart (DotDNAUltraNumber) on the metric query', () => {
    const q = generateChartQuery('DotDNAUltraNumber', {
      metricType: 'filtered',
    });

    expect(q!.queryType).toBe('metric');
    expect(q!.select.metric).toEqual({ agg: 'COUNT' });
    expect(q!.select.timeField).toBeUndefined();
  });
});
