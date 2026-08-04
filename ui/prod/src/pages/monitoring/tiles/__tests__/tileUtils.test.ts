// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { defaultSeriesLabel, extractScalar, mapMatrixToLineChart } from '../tileUtils';

describe('defaultSeriesLabel', () => {
  it('returns an empty string when only target-identifying labels remain', () => {
    // The Redis/Database legend-dump bug: a lone series should get no legend,
    // not `__name__=…,instance=…,job=…`.
    expect(defaultSeriesLabel({
      __name__: 'redis_memory_used_bytes', instance: 'redis-exporter:9121', job: 'redis-exporter',
    })).toBe('');
  });

  it('uses the single meaningful label value', () => {
    expect(defaultSeriesLabel({
      __name__: 'pg_stat_activity_count', instance: 'x', job: 'y', state: 'idle',
    })).toBe('idle');
  });

  it('joins multiple meaningful labels compactly', () => {
    expect(defaultSeriesLabel({ state: 'idle', datname: 'assetatlas' })).toBe('state=idle, datname=assetatlas');
  });
});

describe('extractScalar', () => {
  it('parses the instant-vector value', () => {
    expect(extractScalar({ resultType: 'vector', result: [{ metric: {}, value: [0, '42'] }] } as any)).toBe(42);
  });
  it('returns null for an empty vector', () => {
    expect(extractScalar({ resultType: 'vector', result: [] } as any)).toBeNull();
  });
});

describe('mapMatrixToLineChart', () => {
  it('labels each series via the supplied label fn and drops NaN points', () => {
    const out = mapMatrixToLineChart(
      { resultType: 'matrix', result: [{ metric: { dir: 'rx' }, values: [[0, '1'], [60, 'NaN']] }] } as any,
      (m) => m.dir ?? '',
    );
    expect(out).toEqual([{ series: 'rx', label: expect.any(String), value: 1 }]);
  });
});
