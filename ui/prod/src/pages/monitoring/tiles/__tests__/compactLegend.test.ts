// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach } from 'vitest';
// Side-effect imports: the vanilla chart classes register themselves on window
// (jsdom). SVGChartBase must load first — StackedColumn/LineChart extend it.
import '../../../../../public/dotdna_charts_js/charts/SVGChartBase.js';
import '../../../../../public/dotdna_charts_js/charts/StackedColumn.js';
import '../../../../../public/dotdna_charts_js/charts/LineChart.js';

const StackedColumn = (window as any).DotDNAStackedColumn as new (id: string, opts: any) => any;
const LineChart = (window as any).DotDNALineChart as new (id: string, opts: any) => any;

// jsdom doesn't implement SVG geometry methods; LineChart's line-draw animation
// calls path.getTotalLength() during render. Stub it so the chart renders in the
// test env (the value is irrelevant to legend layout).
if (!(SVGElement.prototype as any).getTotalLength) {
  (SVGElement.prototype as any).getTotalLength = () => 0;
}

const STACK_DATA = [
  { label: 't1', segments: [{ label: 'container-a', value: 1 }, { label: 'container-b', value: 2 }] },
  { label: 't2', segments: [{ label: 'container-a', value: 3 }, { label: 'container-b', value: 1 }] },
];
const LINE_DATA = [
  { series: 'container-a', label: '10:00', value: 1 },
  { series: 'container-a', label: '10:01', value: 2 },
  { series: 'container-b', label: '10:00', value: 3 },
  { series: 'container-b', label: '10:01', value: 4 },
];
// A dense multi-series line dataset (>30 points/series) — like the per-container
// monitoring charts, which skip per-point dots and rely on the crosshair.
const DENSE_LINE_DATA = Array.from({ length: 40 }, (_, i) => [
  { series: 'container-a', label: `t${i}`, value: (i % 5) + 1 },
  { series: 'container-b', label: `t${i}`, value: ((i + 2) % 5) + 1 },
]).flat();
// One column per timestamp, as a 1h/15s range produces (240 buckets).
const DENSE_DATA = Array.from({ length: 240 }, (_, i) => ({
  label: `t${i}`,
  segments: [
    { label: 'container-a', value: (i % 5) + 1 },
    { label: 'container-b', value: ((i + 2) % 5) + 1 },
  ],
}));

function mount(Cls: new (id: string, opts: any) => any, opts: any) {
  document.body.innerHTML = '<div id="chart" style="width:600px;height:300px"></div>';
  return new Cls('chart', opts);
}

// Regression: the chart constructors rebuild this.config from a fixed key
// whitelist. compactLegend was omitted, so `if (this.config.compactLegend)` in
// the legend code never ran and the monitoring legends wrapped to several rows
// instead of a single scrollable line. These assert the flag survives the
// constructor AND actually drives the legend layout.
describe('compactLegend → single-row legend', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('StackedColumn: compactLegend survives the constructor and makes the legend a non-wrapping row', () => {
    const chart = mount(StackedColumn, { compactLegend: true, data: STACK_DATA });
    expect(chart.config.compactLegend).toBe(true);
    expect(chart.legendContainer.style.flexWrap).toBe('nowrap');
    expect(chart.legendContainer.style.overflowX).toBe('auto');
  });

  it('StackedColumn: without compactLegend the legend keeps its default wrap (Reporting/chat unchanged)', () => {
    const chart = mount(StackedColumn, { data: STACK_DATA });
    expect(chart.config.compactLegend).toBe(false);
    expect(chart.legendContainer.style.flexWrap).toBe('wrap');
    expect(chart.legendContainer.style.overflowX).toBe('');
  });

  it('LineChart: compactLegend survives the constructor and makes the legend a non-wrapping row', () => {
    const chart = mount(LineChart, { compactLegend: true, data: LINE_DATA });
    expect(chart.config.compactLegend).toBe(true);
    expect(chart.legendContainer.style.flexWrap).toBe('nowrap');
    expect(chart.legendContainer.style.overflowX).toBe('auto');
  });

  it('LineChart: without compactLegend the legend keeps its default wrap', () => {
    const chart = mount(LineChart, { data: LINE_DATA });
    expect(chart.config.compactLegend).toBe(false);
    expect(chart.legendContainer.style.flexWrap).toBe('wrap');
  });
});

// Regression: StackedColumn gave every column a fixed 8px gap. With one column
// per timestamp (240 at 1h/15s) the gaps alone (~1912px) exceeded the plot width,
// squeezing every column to zero width — a blank chart. The gap must scale down
// with the column count.
describe('StackedColumn dense time-series → columns keep width', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('collapses the inter-column gap for a dense series (240 columns)', () => {
    const chart = mount(StackedColumn, { data: DENSE_DATA });
    expect(chart.columnsContainer.style.gap).toBe('0px');
  });

  it('keeps the roomy 8px gap for a few-column categorical chart (Reporting unchanged)', () => {
    const chart = mount(StackedColumn, { data: STACK_DATA });
    expect(chart.columnsContainer.style.gap).toBe('8px');
  });
});

// The monitoring dashboards auto-refresh (default 30s). With the entry animation
// on, every refetch replayed a multi-second line-draw — intrusive. animate:false
// runs the reveals synchronously (no scheduled timers) so the redraw is instant.
describe('LineChart animate:false → seamless (no entry animation) redraws', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('honours animate:false and schedules no animation timers', () => {
    const chart = mount(LineChart, { animate: false, data: LINE_DATA });
    expect(chart.config.animate).toBe(false);
    expect(chart._pendingTimers.length).toBe(0);
  });

  it('animates by default (Reporting/chat keep their entry animation)', () => {
    const chart = mount(LineChart, { data: LINE_DATA });
    expect(chart.config.animate).toBe(true);
    expect(chart._pendingTimers.length).toBeGreaterThan(0);
  });
});

// Dense series skip per-point dots, so there is nothing to hover for a tooltip.
// The crosshair overlay fills that gap — but only for dense series, so sparse
// charts (Reporting/chat) keep their per-point dot tooltips.
describe('LineChart crosshair hover tooltip', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('arms the crosshair overlay + hover model for a dense series (>30 points)', () => {
    const chart = mount(LineChart, { animate: false, data: DENSE_LINE_DATA });
    expect(chart._hover).not.toBeNull();
    expect(chart._hover.seriesList.length).toBe(2);
    expect(chart.hoverOverlay.style.pointerEvents).toBe('auto');
  });

  it('leaves the overlay off for a sparse series (per-point dots handle hover)', () => {
    const chart = mount(LineChart, { animate: false, data: LINE_DATA });
    expect(chart._hover).toBeNull();
    expect(chart.hoverOverlay.style.pointerEvents).toBe('none');
  });
});

// The density heuristic made per-container charts inconsistent — full-history
// charts (>30 pts) were smooth, short-history ones got dots. showDots:false
// (monitoring) removes dots everywhere and always uses the crosshair.
describe('LineChart showDots:false → smooth lines everywhere', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('renders no per-point dots and arms the crosshair even for sparse data', () => {
    const chart = mount(LineChart, { animate: false, showDots: false, data: LINE_DATA });
    expect(chart.pointsContainer.children.length).toBe(0);
    expect(chart._hover).not.toBeNull();
    expect(chart.hoverOverlay.style.pointerEvents).toBe('auto');
  });

  it('still renders per-point dots for sparse data by default (Reporting unchanged)', () => {
    const chart = mount(LineChart, { data: LINE_DATA });
    expect(chart.pointsContainer.children.length).toBeGreaterThan(0);
  });
});
