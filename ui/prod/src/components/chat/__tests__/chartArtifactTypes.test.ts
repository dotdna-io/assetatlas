// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import {
  parseArtifact,
  toDotDNADonut,
  toDotDNAStackedColumn,
  type DonutArtifact,
} from '../chartArtifactTypes';
import {
  makeDonutArtifact,
  makeStackedBarArtifact,
  makeTableArtifact,
} from './fixtures';


function donut(
  series: DonutArtifact['series'],
  metaOverrides: Partial<DonutArtifact['meta']> = {},
): DonutArtifact {
  return makeDonutArtifact({ series, meta: metaOverrides });
}


describe('toDotDNADonut', () => {
  it('converts raw counts into percentages that sum to ~100', () => {
    const a = donut([
      { label: 'server', value: 6 },
      { label: 'workstation', value: 4 },
    ]);

    const out = toDotDNADonut(a.series, a.meta);

    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({
      name: 'server',
      value: 60,
      count: 6,
    });
    expect(out[1]).toMatchObject({
      name: 'workstation',
      value: 40,
      count: 4,
    });
    const totalPct = out.reduce((acc, d) => acc + d.value, 0);
    expect(totalPct).toBeCloseTo(100, 0);
  });

  it('rounds percentages to one decimal', () => {
    const a = donut([
      { label: 'a', value: 1 },
      { label: 'b', value: 3 },
    ]);
    const out = toDotDNADonut(a.series, a.meta);
    expect(out[0].value).toBe(25);
    expect(out[1].value).toBe(75);
  });

  it('assigns two-tone gradients from the primary palette', () => {
    const a = donut([{ label: 'x', value: 10 }]);
    const out = toDotDNADonut(a.series, a.meta);
    expect(out[0].gradient).toHaveLength(2);
    expect(out[0].gradient[0]).toMatch(/^#[0-9A-Fa-f]{6}$/);
    expect(out[0].gradient[1]).toMatch(/^#[0-9A-Fa-f]{6}$/);
    // The darker shade must actually differ — otherwise the donut's
    // radial gradient renders flat and the slices look washed-out.
    expect(out[0].gradient[0]).not.toBe(out[0].gradient[1]);
  });

  it('maps "Other" to the muted slate gradient regardless of position', () => {
    const a = donut([
      { label: 'a', value: 10 },
      { label: 'Other', value: 3 },
    ]);
    const out = toDotDNADonut(a.series, a.meta);
    const other = out.find((d) => d.name === 'Other')!;
    expect(other.gradient).toEqual(['#6B7280', '#4B5563']);
    // Non-"Other" slices must not use the muted hue — guards against a
    // palette index off-by-one that would paint a real category grey.
    const regular = out.find((d) => d.name !== 'Other')!;
    expect(regular.gradient[0]).not.toBe('#6B7280');
  });

  it('cycles palette hues when series exceeds palette length', () => {
    const labels = [
      'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i',
    ];
    const a = donut(labels.map((label) => ({ label, value: 1 })));
    const out = toDotDNADonut(a.series, a.meta);
    // Same length, no crash. With 7-color palette and 9 items, positions
    // 7 and 8 reuse palette[0] and palette[1].
    expect(out).toHaveLength(labels.length);
    expect(out[7].gradient).toEqual(out[0].gradient);
    expect(out[8].gradient).toEqual(out[1].gradient);
  });

  it('returns zero percentages when total is zero (defensive)', () => {
    // The backend builder never emits this, but a hypothetical future
    // caller with a corrupted envelope should not divide by zero.
    const out = toDotDNADonut(
      [{ label: 'x', value: 0 }],
      {
        source_tool: 'aggregate_assets',
        total: 0,
        other_count: 0,
        row_field: 'category',
      },
    );
    expect(out[0].value).toBe(0);
    expect(out[0].count).toBe(0);
  });
});


describe('parseArtifact', () => {
  it('accepts a well-formed donut envelope', () => {
    const raw = donut([{ label: 'a', value: 1 }]);
    expect(parseArtifact(raw)).toBe(raw);
  });

  it('rejects null/undefined/primitives', () => {
    expect(parseArtifact(null)).toBeNull();
    expect(parseArtifact(undefined)).toBeNull();
    expect(parseArtifact('donut')).toBeNull();
    expect(parseArtifact(42)).toBeNull();
  });

  it('rejects unknown discriminant types', () => {
    expect(parseArtifact({ ...donut([]), type: 'bar' })).toBeNull();
  });

  it('rejects envelopes with wrong-typed fields', () => {
    const base = donut([{ label: 'a', value: 1 }]);
    expect(parseArtifact({ ...base, title: 42 })).toBeNull();
    expect(parseArtifact({ ...base, series: 'not an array' })).toBeNull();
    expect(parseArtifact({ ...base, meta: 'not an object' })).toBeNull();
  });
});


describe('parseArtifact — stacked_bar', () => {
  it('narrows a valid stacked_bar envelope', () => {
    const out = parseArtifact(makeStackedBarArtifact());
    expect(out?.type).toBe('stacked_bar');
  });

  it('returns null when type matches but series is missing', () => {
    const a = makeStackedBarArtifact();
    delete (a as any).series;
    expect(parseArtifact(a)).toBeNull();
  });
});

describe('parseArtifact — table', () => {
  it('narrows a valid table envelope', () => {
    const out = parseArtifact(makeTableArtifact());
    expect(out?.type).toBe('table');
  });

  it('returns null when columns is missing', () => {
    const a = makeTableArtifact();
    delete (a as any).columns;
    expect(parseArtifact(a)).toBeNull();
  });

  it('returns null when columns is not an array', () => {
    const a = makeTableArtifact();
    (a as any).columns = 'nope';
    expect(parseArtifact(a)).toBeNull();
  });
});

describe('parseArtifact — unknown type', () => {
  it('returns null for unknown discriminants', () => {
    const a = { ...makeDonutArtifact(), type: 'pie' };
    expect(parseArtifact(a)).toBeNull();
  });
});

describe('toDotDNAStackedColumn', () => {
  it('preserves x_label and segment label/value', () => {
    const data = toDotDNAStackedColumn(makeStackedBarArtifact().series);
    expect(data).toEqual([
      { label: 'server',      segments: [
        { value: 3, label: 'win' }, { value: 2, label: 'linux' },
      ]},
      { label: 'workstation', segments: [
        { value: 4, label: 'win' }, { value: 1, label: 'linux' },
      ]},
    ]);
  });

  it('drops zero-value segments', () => {
    const series = [{ x_label: 'r1',
      stacks: [{ label: 'a', value: 5 }, { label: 'b', value: 0 }] }];
    const data = toDotDNAStackedColumn(series);
    expect(data[0].segments).toEqual([{ value: 5, label: 'a' }]);
  });

  it('produces an empty segments array when all stacks are zero', () => {
    const series = [{ x_label: 'r1', stacks: [{ label: 'a', value: 0 }] }];
    const data = toDotDNAStackedColumn(series);
    expect(data).toHaveLength(1);
    expect(data[0]).toEqual({ label: 'r1', segments: [] });
  });
});
