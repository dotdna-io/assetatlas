// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
// Side-effect imports: the vanilla charts register themselves on window (jsdom).
// SVGChartBase must load first — the chart class extends the global it defines.
import '../../../../public/dotdna_charts_js/charts/SVGChartBase.js';
import '../../../../public/dotdna_charts_js/charts/MetricCard.js';

const DotDNAMetricCard = (window as any).DotDNAMetricCard as new (
  id: string,
  opts: any,
) => any;

// Shape produced by transformAPIDataForChart for metric trend cards.
const TREND_DATA = {
  currentValue: 871,
  trend: [
    { label: '2026-06-22', value: 635 },
    { label: '2026-07-06', value: 635 },
    { label: '2026-07-20', value: 871 },
  ],
  stats: [
    { label: '2026-06-22', value: '635' },
    { label: '2026-07-20', value: '871', highlight: true },
  ],
};

beforeEach(() => {
  document.body.innerHTML = '<div id="card"></div>';
  // Keep the counter/entrance animations out of jsdom.
  vi.stubGlobal('requestAnimationFrame', () => 0);
  // jsdom doesn't implement SVG geometry — the line-draw animation needs it.
  (SVGElement.prototype as any).getTotalLength = () => 100;
});

describe('DotDNAMetricCard layout', () => {
  it('draws the starting value inside the chart, not in the stats row', () => {
    new DotDNAMetricCard('card', { data: TREND_DATA });

    const startLabel = document.querySelector('#card .mc-start-value');
    expect(startLabel?.textContent).toBe('635');

    const statsRow = document.querySelector('#card .mc-stats');
    expect(statsRow?.textContent).toContain('2026-06-22');
    expect(statsRow?.textContent).not.toContain('635');
  });

  it('omits the ending value from the stats row — it duplicates the headline number', () => {
    new DotDNAMetricCard('card', { data: TREND_DATA });

    const statsRow = document.querySelector('#card .mc-stats');
    expect(statsRow?.textContent).toContain('2026-07-20');
    expect(statsRow?.textContent).not.toContain('871');
  });

  it('uses the compact layout in a short (1-row card) container so the stats row fits', () => {
    const container = document.getElementById('card') as HTMLElement;
    // A 2x1 grid card gives the chart body ~135px (162px track minus name bar).
    Object.defineProperty(container, 'clientHeight', { value: 135, configurable: true });
    new DotDNAMetricCard('card', { data: TREND_DATA });

    const big = document.querySelector('#card .mc-value') as HTMLElement;
    const wrapper = container.firstElementChild as HTMLElement;
    // The default 54px number + 26px top padding overflow a 135px body and
    // clip the date row (chartBody hides overflow) — compact must kick in.
    expect(parseInt(big.style.fontSize)).toBeLessThanOrEqual(40);
    expect(parseInt(wrapper.style.paddingTop)).toBeLessThanOrEqual(12);
  });

  it('keeps the large layout when the container is tall (editor preview)', () => {
    const container = document.getElementById('card') as HTMLElement;
    Object.defineProperty(container, 'clientHeight', { value: 360, configurable: true });
    new DotDNAMetricCard('card', { data: TREND_DATA });

    const big = document.querySelector('#card .mc-value') as HTMLElement;
    expect(big.style.fontSize).toBe('54px');
  });

  it('renders no in-chart start value for a single-bucket trend', () => {
    new DotDNAMetricCard('card', {
      data: {
        currentValue: 871,
        trend: [{ label: '2026-07-20', value: 871 }],
        stats: [{ label: '2026-07-20', value: '871', highlight: true }],
      },
    });

    expect(document.querySelector('#card .mc-start-value')).toBeNull();
  });
});
