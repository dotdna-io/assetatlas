// SPDX-License-Identifier: AGPL-3.0-or-later
// Artifact types and DotDNA adapter for chat chart rendering.
//
// Tool calls in a chat turn may emit a structured envelope that the
// chatbot UI renders inline below the assistant's text. The envelope is
// discriminated by ``type`` — v1 ships ``donut``; bar/line/stacked_bar
// extend the union without a backend schema change.
//
// Backend contract: src/api/blueprints/chat/artifacts.py.

export interface DonutArtifact {
  type: 'donut';
  title: string;
  series: Array<{ label: string; value: number }>;
  meta: {
    source_tool: string;
    total: number;
    other_count: number;
    row_field?: string;
  };
}

export interface StackedBarArtifact {
  type: 'stacked_bar';
  title: string;
  series: Array<{
    x_label: string;
    stacks: Array<{ label: string; value: number }>;
  }>;
  meta: {
    source_tool: 'aggregate_assets';
    total: number;
    row_field: string;
    col_field: string;
  };
}

export interface TableArtifact {
  type: 'table';
  title: string;
  columns: Array<{ key: string; label: string }>;
  series: Array<Record<string, unknown>>;
  meta: {
    source_tool:
      | 'get_asset'
      | 'list_assets'
      | 'search_assets'
      | 'aggregate_assets'
      | 'query_assets'
      | 'export_assets';
    // 'aggregate' / 'field_values' are emitted by query_assets / export_assets;
    // 'aggregate' routes the CSV download to /api/assets/aggregate.csv.
    layout: 'keyvalue' | 'list' | 'aggregate' | 'field_values';
    total?: number;
    truncated?: boolean;
    asset_id?: string;
    row_field?: string;
    col_field?: string | null;
    // Originating query, echoed by the backend so the table can re-run it for a
    // full-set CSV download. For layout 'list' it is a list query (incl the
    // chosen `fields` columns); for layout 'aggregate' it is the pivot spec.
    // Opaque to the renderer beyond picking the download route.
    query?: {
      filters?: unknown;
      search?: string | null;
      sort_by?: string | null;
      sort_dir?: string | null;
      fields?: string | null;
      logic?: string | null;
      // aggregate export (layout 'aggregate'):
      row_field?: string | null;
      col_field?: string | null;
      agg?: string | null;
      metric_field?: string | null;
      show?: string | null;
    };
  };
}

// Discriminated union so new chart types light up exhaustive switches.
export type ChartArtifact = DonutArtifact | StackedBarArtifact | TableArtifact;


export interface DotDNADonutDatum {
  name: string;
  value: number;   // percentage (0-100), one decimal
  count: number;   // raw count (for legend/tooltip)
  gradient: [string, string];
}


// Matches the DotDNAAnimatedDonutModern default palette so chat charts
// read as part of the same visual family as reports. "Other" is mapped
// to a muted slate gradient so the rolled-up bucket is visually distinct
// from real categories.
const PALETTE: ReadonlyArray<[string, string]> = [
  ['#3B82F6', '#2563EB'], // Blue
  ['#10B981', '#059669'], // Green
  ['#F59E0B', '#D97706'], // Amber
  ['#EC4899', '#DB2777'], // Rose
  ['#8B5CF6', '#7C3AED'], // Purple
  ['#06B6D4', '#0891B2'], // Teal
  ['#6366F1', '#4F46E5'], // Indigo
];

const OTHER_GRADIENT: [string, string] = ['#6B7280', '#4B5563'];


/**
 * Translate the generic ``{label, value}`` donut series into the shape
 * the DotDNAAnimatedDonutModern vanilla class expects.
 *
 * - ``value`` goes out as a **percentage** (0-100, one decimal); the raw
 *   count is preserved on ``count`` for legend + tooltip.
 * - Colors cycle through the DotDNA primary palette by position.
 * - An ``"Other"`` slice always gets the muted slate gradient regardless
 *   of its position, so users can spot the rollup bucket at a glance.
 *
 * The builder guarantees ``total > 0`` when it emits a donut, but we
 * guard here too so a hypothetical future caller doesn't divide by zero.
 */
export function toDotDNADonut(
  series: DonutArtifact['series'],
  meta: DonutArtifact['meta'],
): DotDNADonutDatum[] {
  const total = meta.total > 0 ? meta.total : 0;
  let paletteIdx = 0;
  return series.map((slice) => {
    const isOther = slice.label === 'Other';
    const gradient: [string, string] = isOther
      ? OTHER_GRADIENT
      : PALETTE[paletteIdx++ % PALETTE.length];
    const percent = total > 0 ? +((slice.value / total) * 100).toFixed(1) : 0;
    return {
      name: slice.label,
      value: percent,
      count: slice.value,
      gradient,
    };
  });
}


export interface DotDNAStackedColumnDatum {
  label: string;
  segments: Array<{ value: number; label: string }>;
}

/** Translate the artifact's stacked-bar series into the shape the
 *  DotDNAStackedColumn vanilla class expects:
 *  ``[{ label, segments: [{ value, label }] }]``. Zero-value segments
 *  are dropped so the chart doesn't render invisible slivers. */
export function toDotDNAStackedColumn(
  series: StackedBarArtifact['series'],
): DotDNAStackedColumnDatum[] {
  return series.map((bar) => ({
    label: bar.x_label,
    segments: bar.stacks
      .filter((s) => s.value > 0)
      .map((s) => ({ value: s.value, label: s.label })),
  }));
}


// Narrow ``unknown`` (the shape tool_calls entries arrive in from
// ChatMessage) to a ChartArtifact, or return null if the payload is
// missing or doesn't match a known discriminant. Keeps the validation
// in one place so Message.tsx / ChartArtifact.tsx don't re-check.
export function parseArtifact(raw: unknown): ChartArtifact | null {
  if (!raw || typeof raw !== 'object') return null;
  const a = raw as Record<string, unknown>;
  if (typeof a.title !== 'string') return null;
  if (!Array.isArray(a.series)) return null;
  if (!a.meta || typeof a.meta !== 'object') return null;
  if (a.type === 'donut') return a as unknown as DonutArtifact;
  if (a.type === 'stacked_bar') return a as unknown as StackedBarArtifact;
  if (a.type === 'table') {
    if (!Array.isArray((a as Record<string, unknown>).columns)) return null;
    return a as unknown as TableArtifact;
  }
  return null;
}


// Pull the ``artifact`` off a persisted tool_call entry (shape is
// ``unknown`` from ChatMessage.tool_calls) and validate it. Single
// accessor so Message/MessageList don't each redo the Record-cast.
export function artifactFromToolCall(tc: unknown): ChartArtifact | null {
  if (!tc || typeof tc !== 'object') return null;
  return parseArtifact((tc as Record<string, unknown>).artifact);
}
