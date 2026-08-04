// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { transformAPIDataForChart } from '../chartDataPipeline';

const chart = (qt: string) => ({
  className: 'DotDNACleanSegmentedVerticalBar',
  queryConfig: { queryType: qt, select: {} },
});

describe('Chronicle Bar history_trend transform', () => {
  it('single series → bars over time', () => {
    const rows = [
      { timestamp: '2026-01-01T00:00:00', value: 10 },
      { timestamp: '2026-01-02T00:00:00', value: 12 },
    ];
    const out: any = transformAPIDataForChart(rows, chart('history_trend'));
    expect(Array.isArray(out)).toBe(true);
    expect(out[0]).toMatchObject({ name: '2026-01-01', value: 10 });
  });
  it('split → grouped bars by period', () => {
    const rows = [
      { timestamp: '2026-01-01T00:00:00', series: 'A', value: 3, color: '#111' },
      { timestamp: '2026-01-01T00:00:00', series: 'B', value: 4, color: '#222' },
    ];
    const out: any = transformAPIDataForChart(rows, chart('history_trend'));
    expect(out.mode).toBe('grouped');
    expect(out.groups[0].name).toBe('2026-01-01');
    expect(out.legend.length).toBe(2);
  });
  it('live Vertical Bar (distribution) unaffected', () => {
    const rows = [{ label: 'x', value: 5, color: '#333' }];
    const out: any = transformAPIDataForChart(rows, {
      className: 'DotDNACleanSegmentedVerticalBar',
      queryConfig: { queryType: 'distribution', select: {} },
    });
    expect(out[0]).toMatchObject({ name: 'x', value: 5 });
  });
});
