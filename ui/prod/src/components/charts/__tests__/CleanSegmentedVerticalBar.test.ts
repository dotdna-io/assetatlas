// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach } from 'vitest';
// Side-effect imports: the vanilla charts register themselves on window (jsdom).
// SVGChartBase must load first — the chart class extends the global it defines.
import '../../../../public/dotdna_charts_js/charts/SVGChartBase.js';
import '../../../../public/dotdna_charts_js/charts/CleanSegmentedVerticalBar.js';

const DotDNACleanSegmentedVerticalBar = (window as any).DotDNACleanSegmentedVerticalBar as new (
  id: string,
  opts: any,
) => any;

// A wide distribution with long names — enough entries that untruncated
// labels overflow the chart and drift out of alignment with their bars.
const SUBCATEGORY_DATA = [
  'unknown',
  'laptop',
  'server',
  'desktop',
  'phone',
  'virtual_desktop',
  'virtual_server',
  'Other',
  'switch',
  'firewall',
  'hypervisor',
  'physical_server',
].map((name, i) => ({ name, value: 1000 - i * 80, color: '#3B82F6' }));

function xAxisLabels(container: HTMLElement): HTMLElement[] {
  return SUBCATEGORY_DATA.map((d) => {
    const el = Array.from(container.querySelectorAll('div')).find(
      (n) => n.textContent === d.name && n.children.length === 0,
    );
    expect(el, `x-axis label for "${d.name}" not rendered`).toBeTruthy();
    return el as HTMLElement;
  });
}

beforeEach(() => {
  document.body.innerHTML = '<div id="chart"></div>';
});

describe('DotDNACleanSegmentedVerticalBar x-axis labels', () => {
  it('truncates long labels instead of letting them widen past their bar column', () => {
    new DotDNACleanSegmentedVerticalBar('chart', { data: SUBCATEGORY_DATA });

    const container = document.getElementById('chart')!;
    for (const label of xAxisLabels(container)) {
      // The ellipsis contract (same as grouped mode): without it a long name
      // can't shrink below its text width, pushing the whole labels row wider
      // than the bars row — labels spill outside the chart and misalign.
      expect(label.style.overflow).toBe('hidden');
      expect(label.style.textOverflow).toBe('ellipsis');
      expect(label.style.whiteSpace).toBe('nowrap');
      // Full name stays readable on hover.
      expect(label.title).toBe(label.textContent);
    }
  });
});
