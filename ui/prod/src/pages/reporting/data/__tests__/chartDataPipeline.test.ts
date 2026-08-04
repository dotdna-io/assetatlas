// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import {
  transformToAPIRequest,
  transformAPIDataForChart,
} from '../chartDataPipeline';
import type { ChartFilter } from '@/api/charts';

/**
 * The backend FilterOperator enum (src/api/blueprints/charts/models.py) has
 * inconsistent wire forms: most members use underscores ("not_equals"), but
 * IS_NULL/IS_NOT_NULL keep spaces ("is empty"/"is not empty") and numeric
 * comparators use abbreviations (gt/gte/lt/lte/in). The chart wizard's
 * FilterRow always emits underscored display strings, so the pipeline must
 * translate those forms back to whatever the backend enum expects — otherwise
 * Pydantic rejects the request with a 400.
 */
describe('transformToAPIRequest — operator translation', () => {
  const fid = (name: string) => ({ kind: 'static' as const, name });

  function configWithFilter(filter: ChartFilter) {
    return {
      queryType: 'metric',
      from: { table: 'assets' },
      select: { metric: { agg: 'COUNT' } },
      filters: [filter],
      options: {},
    };
  }

  it.each([
    ['is_not_empty', 'is not empty'],
    ['is_empty', 'is empty'],
    ['greater_than', 'gt'],
    ['greater_than_or_equal', 'gte'],
    ['less_than', 'lt'],
    ['less_than_or_equal', 'lte'],
    ['any_of', 'in'],
    ['not_equals', 'not_equals'],
    ['starts_with', 'starts_with'],
    ['equals', 'equals'],
  ])('translates underscore form %s -> backend wire form %s', (input, expected) => {
    const req = transformToAPIRequest(
      configWithFilter({ field_id: fid('Category'), operator: input, value: '' }),
    );
    expect(req.filters?.[0].operator).toBe(expected);
  });

  it.each([
    ['not equals', 'not_equals'],
    ['greater than', 'gt'],
    ['less than or equal', 'lte'],
    ['any of', 'in'],
    ['is empty', 'is empty'],
    ['is not empty', 'is not empty'],
  ])('still translates spaced form %s -> %s', (input, expected) => {
    const req = transformToAPIRequest(
      configWithFilter({ field_id: fid('Category'), operator: input, value: '' }),
    );
    expect(req.filters?.[0].operator).toBe(expected);
  });

  it.each([
    // Canonical registry codes (FilterRow emits these for the reporting filter
    // builder too, now that it shares the operator_specs mechanism). They must
    // map to the charts FilterOperator enum or Pydantic 422s.
    ['eq', 'equals'],
    ['neq', 'not_equals'],
    ['ncontains', 'not_contains'],
    ['starts', 'starts_with'],
    ['ends', 'ends_with'],
    ['empty', 'is empty'],
    ['notempty', 'is not empty'],
    // Set membership + range exposed in the reporting menu (E-1).
    ['any', 'in'],
    ['none', 'not_in'],
    // contains/gt/gte/lt/lte/between are identity (already equal the charts enum value).
    ['contains', 'contains'],
    ['gt', 'gt'],
    ['gte', 'gte'],
    ['lt', 'lt'],
    ['lte', 'lte'],
    ['between', 'between'],
  ])('translates canonical code %s -> backend wire form %s', (input, expected) => {
    const req = transformToAPIRequest(
      configWithFilter({ field_id: fid('Category'), operator: input, value: '' }),
    );
    expect(req.filters?.[0].operator).toBe(expected);
  });

  it('translates operators in comparisonFilters too', () => {
    const cfg = {
      queryType: 'metric',
      from: { table: 'assets' },
      select: { metric: { agg: 'COUNT' } },
      filters: [
        { field_id: fid('Microsoft Defender API'), operator: 'is_not_empty', value: '' },
      ],
      comparisonFilters: [
        { field_id: fid('Category'), operator: 'equals', value: 'workstation' },
      ],
      options: { metricType: 'percentage', showPercentage: true },
    };
    const req = transformToAPIRequest(cfg);
    expect(req.filters?.[0].operator).toBe('is not empty');
    expect(req.comparisonFilters?.[0].operator).toBe('equals');
  });
});

/**
 * #946: Metric Spark (DotDNAMetricCard) & Metric Bar (DotDNAMetricBar) render
 * `{currentValue, trend: [{label, value}], stats}` (see MetricCard.js /
 * MetricBar.js). Their trend query returns single-series rows
 * `{timestamp, value}` sorted ascending; this transform must reshape them so
 * the sparkline / mini-bars + first/last stats actually draw. Before #946 the
 * cards used the single-value metric query, so `trend`/`stats` were always
 * empty and the sub-visual collapsed to a bare number.
 */
describe('transformAPIDataForChart — metric trend cards (#946)', () => {
  const trendMetricCharts = ['DotDNAMetricCard', 'DotDNAMetricBar'];

  // Shape the backend returns for a single-series trend query (chart_repository
  // formats the DATE_TRUNC bucket via datetime.isoformat()).
  const trendRows = [
    { timestamp: '2026-01-01T00:00:00', value: 1000 },
    { timestamp: '2026-01-08T00:00:00', value: 1500 },
    { timestamp: '2026-01-15T00:00:00', value: 2000 },
  ];

  it.each(trendMetricCharts)(
    '%s maps trend rows to {currentValue, trend, stats}',
    (className) => {
      const out = transformAPIDataForChart(trendRows, {
        className,
        queryConfig: { queryType: 'trend' },
        name: 'Assets over time',
      });

      expect(Array.isArray(out)).toBe(true);
      const item = out[0];

      // Headline = latest bucket, kept a raw number (the card formats it).
      expect(item.currentValue).toBe(2000);
      expect(typeof item.currentValue).toBe('number');

      // The sparkline / mini-bars need the full series as {label, value:number}.
      expect(item.trend).toEqual([
        { label: '2026-01-01', value: 1000 },
        { label: '2026-01-08', value: 1500 },
        { label: '2026-01-15', value: 2000 },
      ]);

      // Stats: first bucket + last bucket (highlighted), values pre-formatted
      // (K/M) to match how the card renders stats[].value as literal text.
      expect(item.stats).toEqual([
        { label: '2026-01-01', value: '1K' },
        { label: '2026-01-15', value: '2K', highlight: true },
      ]);
    },
  );

  it('yields a sparkline-capable series (>= 2 points) — the #946 regression guard', () => {
    // The card sparkline requires >= 2 trend points; the old single-value
    // metric query produced zero, so the sub-visual never rendered.
    const out = transformAPIDataForChart(trendRows, {
      className: 'DotDNAMetricCard',
      queryConfig: { queryType: 'trend' },
    });
    expect(out[0].trend.length).toBeGreaterThanOrEqual(2);
  });

  it('collapses to a single highlighted stat when only one bucket is returned', () => {
    const out = transformAPIDataForChart(
      [{ timestamp: '2026-01-01T00:00:00', value: 1500 }],
      { className: 'DotDNAMetricCard', queryConfig: { queryType: 'trend' } },
    );
    // A single bucket makes first === last; showing it twice reads as a glitch.
    expect(out[0].stats).toEqual([
      { label: '2026-01-01', value: '1.5K', highlight: true },
    ]);
    expect(out[0].currentValue).toBe(1500);
  });

  it('degrades to a zero headline with no series when there are no rows', () => {
    const out = transformAPIDataForChart([], {
      className: 'DotDNAMetricBar',
      queryConfig: { queryType: 'trend' },
    });
    expect(out[0]).toEqual({ currentValue: 0, trend: [], stats: [] });
  });

  it('leaves the plain Number Chart (DotDNAUltraNumber) on the single-value shape', () => {
    const out = transformAPIDataForChart(
      [{ value: 42, displayValue: '42', showPercent: false, percentValue: 0 }],
      {
        className: 'DotDNAUltraNumber',
        queryConfig: { queryType: 'metric' },
        name: 'Total assets',
      },
    );
    expect(out[0].value).toBe(42);
    expect(out[0].trend).toBeUndefined();
    expect(out[0].currentValue).toBeUndefined();
  });
});
