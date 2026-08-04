// SPDX-License-Identifier: AGPL-3.0-or-later
import type { PrometheusQueryResponse } from '@/api/monitoring';

/** Extract a single numeric value from a Prometheus instant `vector` response. */
export function extractScalar(data: PrometheusQueryResponse | undefined): number | null {
  if (!data || data.resultType !== 'vector' || data.result.length === 0) return null;
  const raw = data.result[0]?.value?.[1];
  if (raw === undefined) return null;
  const n = parseFloat(raw);
  return Number.isNaN(n) ? null : n;
}

function fmtHM(tMs: number): string {
  const d = new Date(tMs);
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

/** Prometheus labels that identify the scrape target, not the series. */
const NOISE_LABELS = new Set(['__name__', 'job', 'instance', 'server', 'container', 'pod']);

/**
 * Default series legend when a panel doesn't supply its own `legend`. Drops
 * target-identifying labels so `__name__=redis_memory_used_bytes,instance=…,job=…`
 * becomes a clean name — or an empty string for a lone series (the tile title
 * already names it, and an empty series name makes DotDNALineChart hide the
 * legend box).
 */
export function defaultSeriesLabel(labels: Record<string, string>): string {
  const meaningful = Object.entries(labels).filter(([k]) => !NOISE_LABELS.has(k));
  if (meaningful.length === 0) return '';
  if (meaningful.length === 1) return meaningful[0][1];
  return meaningful.map(([k, v]) => `${k}=${v}`).join(', ');
}

export interface LineChartPoint {
  series: string;
  label: string;
  value: number;
}

/**
 * Map a Prometheus `matrix` response to DotDNALineChart's flat multi-series shape.
 * Each output row is one (series, timestamp, value) triple. NaN values are dropped
 * so the SVG path stays well-formed.
 */
export function mapMatrixToLineChart(
  data: PrometheusQueryResponse | undefined,
  labelFn: (labels: Record<string, string>) => string,
): LineChartPoint[] | null {
  if (!data || data.resultType !== 'matrix') return null;
  const out: LineChartPoint[] = [];
  for (const s of data.result as Array<{ metric: Record<string, string>; values: Array<[number, string]> }>) {
    const series = labelFn(s.metric);
    for (const [t, v] of s.values || []) {
      const num = parseFloat(v);
      if (Number.isNaN(num)) continue;
      out.push({ series, label: fmtHM(t * 1000), value: num });
    }
  }
  return out;
}

export interface StackedColumn {
  label: string;
  segments: Array<{ label: string; value: number }>;
}

/**
 * Map a Prometheus `matrix` response to DotDNAStackedColumn's column-major shape.
 * Columns are bucketed by timestamp; segments are aligned across columns (missing
 * series in a bucket get value=0) so the legend and color assignment stay stable.
 */
export function mapMatrixToStackedColumn(
  data: PrometheusQueryResponse | undefined,
  groupBy: (labels: Record<string, string>) => string,
): StackedColumn[] | null {
  if (!data || data.resultType !== 'matrix') return null;

  const seriesOrder: string[] = [];
  const buckets = new Map<number, Map<string, number>>();

  for (const s of data.result as Array<{ metric: Record<string, string>; values: Array<[number, string]> }>) {
    const series = groupBy(s.metric);
    if (!seriesOrder.includes(series)) seriesOrder.push(series);
    for (const [t, v] of s.values || []) {
      const num = parseFloat(v);
      if (Number.isNaN(num)) continue;
      const tMs = t * 1000;
      let bucket = buckets.get(tMs);
      if (!bucket) {
        bucket = new Map();
        buckets.set(tMs, bucket);
      }
      bucket.set(series, num);
    }
  }

  return [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([tMs, bucket]) => ({
      label: fmtHM(tMs),
      segments: seriesOrder.map(name => ({ label: name, value: bucket.get(name) ?? 0 })),
    }));
}
