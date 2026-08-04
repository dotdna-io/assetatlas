// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
// Side-effect imports: the vanilla charts register themselves on window (jsdom).
// SVGChartBase must load first — the chart classes extend the global it defines.
import '../../../../public/dotdna_charts_js/charts/SVGChartBase.js';
import '../../../../public/dotdna_charts_js/charts/AnimatedPie.js';
import '../../../../public/dotdna_charts_js/charts/AnimatedDonutModern.js';

const DotDNAAnimatedPie = (window as any).DotDNAAnimatedPie as new (id: string, opts: any) => any;
const DotDNAAnimatedDonutModern = (window as any).DotDNAAnimatedDonutModern as new (
  id: string,
  opts: any,
) => any;

// Real category distribution that triggered the bug: the three sub-1% slices
// are thinner than the 5° petalGap, which inverted their wedges — each drew
// backwards across its neighbours and the last one painted covered the rest.
const CATEGORY_DATA = [
  { name: 'workstation', value: 74.01, count: 54575, gradient: ['#EF4444', '#DC2626'] },
  { name: 'miscellaneous', value: 12.11, count: 8928, gradient: ['#F59E0B', '#D97706'] },
  { name: 'server', value: 10.67, count: 7868, gradient: ['#3B82F6', '#2563EB'] },
  { name: 'mobile', value: 2.27, count: 1674, gradient: ['#10B981', '#059669'] },
  { name: 'network', value: 0.78, count: 576, gradient: ['#8B5CF6', '#7C3AED'] },
  { name: 'iot', value: 0.11, count: 79, gradient: ['#F59E0B', '#D97706'] },
  { name: 'ot', value: 0.05, count: 40, gradient: ['#6366F1', '#4F46E5'] },
];

const CX = 250;
const CY = 250;

/** Angle (degrees) of a point around the chart centre, unwrapped to [-90, 270). */
function angleOf(x: number, y: number): number {
  let a = (Math.atan2(y - CY, x - CX) * 180) / Math.PI;
  if (a < -90) a += 360;
  return a;
}

function numbersIn(d: string): number[] {
  return (d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi) || []).map(Number);
}

/** Pie path: M cx cy L x1 y1 A r r 0 laf 1 x2 y2 Z — boundary rays at (x1,y1) → (x2,y2). */
function pieSliceAngles(d: string): { start: number; end: number } {
  const n = numbersIn(d);
  return { start: angleOf(n[2], n[3]), end: angleOf(n[9], n[10]) };
}

/** Donut path: M x1 y1 A oR oR 0 laf 1 x2 y2 L ... — outer arc runs (x1,y1) → (x2,y2). */
function donutSliceAngles(d: string): { start: number; end: number } {
  const n = numbersIn(d);
  return { start: angleOf(n[0], n[1]), end: angleOf(n[7], n[8]) };
}

function sliceAngleList(
  paths: SVGPathElement[],
  parse: (d: string) => { start: number; end: number },
): { start: number; end: number; width: number }[] {
  return paths.map((p) => {
    const { start, end } = parse(p.getAttribute('d') || '');
    return { start, end, width: end - start };
  });
}

function assertSlicesSane(slices: { start: number; end: number; width: number }[]) {
  // One slice per non-zero category.
  expect(slices).toHaveLength(CATEGORY_DATA.length);

  slices.forEach((s, i) => {
    // A slice must never invert (end ray landing before its start ray) — an
    // inverted wedge paints backwards over its neighbours.
    expect(s.width, `slice ${i} (${CATEGORY_DATA[i].name}) is inverted`).toBeGreaterThan(0);
    // Every non-zero category keeps a visible floor, even at 0.05% share.
    expect(
      s.width,
      `slice ${i} (${CATEGORY_DATA[i].name}) is too thin to see`,
    ).toBeGreaterThanOrEqual(1);
  });

  // Slices must be angularly disjoint and in data order — overlap means one
  // category visually hides another.
  for (let i = 1; i < slices.length; i++) {
    const prev = slices[i - 1];
    const cur = slices[i];
    expect(
      Math.min(cur.start, cur.end),
      `slice ${i} (${CATEGORY_DATA[i].name}) overlaps ${CATEGORY_DATA[i - 1].name}`,
    ).toBeGreaterThanOrEqual(Math.max(prev.start, prev.end) - 0.01);
  }

  // Proportions still hold for normal slices: workstation (74%) dominates.
  expect(slices[0].width).toBeGreaterThan(240);
}

beforeEach(() => {
  document.body.innerHTML = '<div id="chart"></div>';
  // Keep the flower/rotation animation loops out of jsdom — geometry is
  // rendered deterministically via updateSlices/updateSegments(1) below.
  vi.stubGlobal('requestAnimationFrame', () => 0);
});

describe('DotDNAAnimatedPie slice geometry', () => {
  it('renders all 7 categories as visible, non-overlapping slices', () => {
    const chart = new DotDNAAnimatedPie('chart', { data: CATEGORY_DATA });
    chart.updateSlices(1);

    const paths = Array.from(chart.slicesGroup.querySelectorAll('path')) as SVGPathElement[];
    assertSlicesSane(sliceAngleList(paths, pieSliceAngles));
  });
});

describe('DotDNAAnimatedDonutModern slice geometry', () => {
  it('renders all 7 categories as visible, non-overlapping segments', () => {
    const chart = new DotDNAAnimatedDonutModern('chart', { data: CATEGORY_DATA });
    chart.updateSegments(1);

    const paths = Array.from(chart.rotatingGroup.querySelectorAll('path')) as SVGPathElement[];
    assertSlicesSane(sliceAngleList(paths, donutSliceAngles));
  });

  it('still renders a single 100% category as a full ring', () => {
    const chart = new DotDNAAnimatedDonutModern('chart', {
      data: [{ name: 'workstation', value: 100, count: 54575, gradient: ['#EF4444', '#DC2626'] }],
    });
    chart.updateSegments(1);

    const paths = Array.from(chart.rotatingGroup.querySelectorAll('path')) as SVGPathElement[];
    expect(paths).toHaveLength(1);
    // Full circle renders as two semicircle arcs (four A commands, two subpaths).
    const arcs = (paths[0].getAttribute('d') || '').match(/A\s/g) || [];
    expect(arcs.length).toBe(4);
  });
});
