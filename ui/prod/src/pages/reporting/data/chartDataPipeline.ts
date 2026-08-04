// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ChartQueryConfig, ChartFilter } from '@/api/charts';
import { coerceFieldId, encodeFieldId } from '@/types/field-id';

/**
 * Whether a queryConfig has enough to fetch/render. history_trend is valid
 * with only options (server buckets by archived_at), so it has no select slot.
 */
export function hasRenderableQuery(queryConfig: Record<string, any> | null | undefined): boolean {
  if (!queryConfig) return false;
  if (queryConfig.queryType === 'history_trend') return true;
  const sel = queryConfig.select ?? {};
  return !!(sel.dimension || sel.metric || sel.timeField || sel.sourceField);
}

// UI operator → backend FilterOperator wire form.
//
// Two UI forms can arrive here:
//   - spaced display form ("not equals", "is not empty") from any caller that
//     bypasses FilterRow normalization
//   - underscore form ("not_equals", "is_not_empty") from FilterRow, which
//     runs `op.toLowerCase().replace(/\s+/g, '_')` before storing the operator
//
// The backend enum (src/api/blueprints/charts/models.py::FilterOperator) is
// inconsistent: most members use underscores, but IS_NULL/IS_NOT_NULL keep
// the spaces ("is empty"/"is not empty") and numeric comparators use
// abbreviations (gt/gte/lt/lte/in). We translate both UI forms here so the
// wire form always matches the backend enum.
const OPERATOR_MAP: Record<string, string> = {
  // Spaced display form (legacy / non-FilterRow callers)
  'not equals': 'not_equals',
  'not contains': 'not_contains',
  'starts with': 'starts_with',
  'ends with': 'ends_with',
  'greater than': 'gt',
  'greater than or equal': 'gte',
  'less than': 'lt',
  'less than or equal': 'lte',
  'any of': 'in',
  // Underscore form (FilterRow.normalizeOperator output) for operators whose
  // backend wire form differs from the underscored display form
  is_empty: 'is empty',
  is_not_empty: 'is not empty',
  greater_than: 'gt',
  greater_than_or_equal: 'gte',
  less_than: 'lt',
  less_than_or_equal: 'lte',
  any_of: 'in',
  // Canonical registry codes — the reporting filter builder (QuestionRenderer)
  // now emits these via operator_specs, sharing the same mechanism as the
  // tag/posture wizards. Map them to the charts FilterOperator enum.
  // contains/gt/gte/lt/lte are omitted: they already equal the enum value.
  eq: 'equals',
  neq: 'not_equals',
  ncontains: 'not_contains',
  starts: 'starts_with',
  ends: 'ends_with',
  any: 'in',
  none: 'not_in',
  empty: 'is empty',
  notempty: 'is not empty',
  // `between` is omitted: its code already equals the charts enum value 'between'.
};

function normalizeFilterList(filterList: ChartFilter[]): ChartFilter[] {
  return filterList.map((filter) => ({
    ...filter,
    operator: OPERATOR_MAP[filter.operator] || filter.operator,
  }));
}

const DATA_SOURCE_KEY = encodeFieldId({ kind: 'static', name: 'Data Source' });

/** Adjust a hex colour brightness by a percentage (-100..+100). */
function adjustColorBrightness(color: string, percent: number): string {
  const hex = color.replace('#', '');

  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);

  const adjust = (value: number): number => {
    const adjusted = value + (value * percent) / 100;
    return Math.max(0, Math.min(255, Math.round(adjusted)));
  };

  const toHex = (value: number): string => {
    const h = value.toString(16);
    return h.length === 1 ? '0' + h : h;
  };

  return '#' + toHex(adjust(r)) + toHex(adjust(g)) + toHex(adjust(b));
}

/** Truncate text to a maximum length, adding ellipsis when needed. */
function truncateLabel(text: unknown, maxLength = 22): string {
  if (!text) return text as string;

  let textStr: string;
  if (Array.isArray(text)) {
    textStr = String(text[0] || '');
  } else {
    textStr = String(text);
  }

  if (textStr.length <= maxLength) return textStr;
  return textStr.substring(0, maxLength - 1) + '\u2026';
}

/**
 * Abbreviate data source names for chart axis labels.
 * - Under maxLength: returned as-is
 * - Over maxLength: abbreviate from word initials
 *   (e.g., "Microsoft SCCM Connector" → "MSC"), fallback to truncation
 */
function abbreviateDataSource(text: unknown, maxLength = 14): string {
  if (!text) return text as string;

  const textStr = Array.isArray(text) ? String(text[0] || '') : String(text);
  if (textStr.length <= maxLength) return textStr;

  const words = textStr.split(/[\s_\-\/]+/).filter(Boolean);
  if (words.length >= 2) {
    const abbr = words.map((w) => w[0].toUpperCase()).join('');
    if (abbr.length <= maxLength) return abbr;
  }

  return textStr.substring(0, maxLength - 1) + '\u2026';
}

/**
 * Format a metric value for the compact stats row on the metric trend cards,
 * mirroring MetricCard.js / MetricBar.js `formatNum` (K/M abbreviation). The
 * cards render `stats[].value` as literal text, so it must be pre-formatted
 * here (unlike `currentValue` / `trend[].value`, which the card keeps numeric).
 */
function formatMetricStat(num: number): string {
  if (Math.abs(num) >= 1_000_000) {
    return (num / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
  }
  if (Math.abs(num) >= 1_000) {
    return (num / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
  }
  return num.toLocaleString();
}

/**
 * Label for a single trend bucket: the date portion of the ISO timestamp the
 * trend query returns (`2026-01-01T00:00:00` -> `2026-01-01`). Falls back to
 * `period` / `label` then the raw value, mirroring the line-chart branch's key
 * precedence.
 */
function trendBucketLabel(row: Record<string, any>): string {
  const raw = row.timestamp || row.period || row.label || '';
  const str = String(raw);
  const tIndex = str.indexOf('T');
  return tIndex > 0 ? str.slice(0, tIndex) : str;
}

// ---------------------------------------------------------------------------
// Request transformation
// ---------------------------------------------------------------------------

/**
 * Transform a UI query config object into the format expected by
 * `POST /api/charts/query`.
 */
export function transformToAPIRequest(
  queryConfig: Record<string, any>,
  filters?: ChartFilter[],
): ChartQueryConfig {
  const { queryType, select = {}, options = {} } = queryConfig;

  // Use explicitly passed filters, or fall back to filters inside queryConfig
  const effectiveFilters: ChartFilter[] =
    filters && filters.length > 0
      ? filters
      : queryConfig.filters || [];

  const normalizedFilters = normalizeFilterList(effectiveFilters);

  // Build base request
  const request: Record<string, any> = {
    queryType,
    from: queryConfig.from || { table: 'assets', joins: [] },
    select: {} as Record<string, any>,
    filters: normalizedFilters,
    options: {
      limit: options.limit || (queryType === 'trend' || queryType === 'trend_percentage' || queryType === 'history_trend' ? 1000 : 100),
      offset: options.offset || 0,
      sortOrder: options.sortOrder || 'desc',
      includeNull: options.includeNull || false,
      calculatePercentage: options.calculatePercentage !== false,
    },
  };

  // Copy select fields. Each spec slot must be a typed FieldId object on
  // the wire (see src/api/blueprints/charts/models.py::SelectClause); the
  // editor still supplies encoded_key strings from <option value=...>, so
  // decode here. Metric is a MetricSpec object — pass through verbatim.
  const dimId = coerceFieldId(select.dimension, 'dimension');
  if (dimId) request.select.dimension = dimId;
  const segId = coerceFieldId(select.segment, 'segment');
  if (segId) request.select.segment = segId;
  if (select.metric) request.select.metric = select.metric;
  const timeFieldId = coerceFieldId(select.timeField, 'timeField');
  if (timeFieldId) request.select.timeField = timeFieldId;
  const xAxisId = coerceFieldId(select.xAxis, 'xAxis');
  if (xAxisId) request.select.xAxis = xAxisId;
  const yAxisId = coerceFieldId(select.yAxis, 'yAxis');
  if (yAxisId) request.select.yAxis = yAxisId;
  const sourceFieldId = coerceFieldId(select.sourceField, 'sourceField');
  if (sourceFieldId) request.select.sourceField = sourceFieldId;
  const targetFieldId = coerceFieldId(select.targetField, 'targetField');
  if (targetFieldId) request.select.targetField = targetFieldId;
  // Punchcard uses rowField/columnField — map to sourceField/targetField
  if (!request.select.sourceField) {
    const rowFieldId = coerceFieldId(select.rowField, 'rowField');
    if (rowFieldId) request.select.sourceField = rowFieldId;
  }
  if (!request.select.targetField) {
    const colFieldId = coerceFieldId(select.columnField, 'columnField');
    if (colFieldId) request.select.targetField = colFieldId;
  }

  // Copy additional options
  if (options.sortBy) request.options.sortBy = options.sortBy;
  if (options.interval) request.options.interval = options.interval;
  // timeRange takes precedence over last (trend template uses timeRange)
  const effectiveLast =
    options.timeRange !== undefined ? options.timeRange : options.last;
  if (effectiveLast) request.options.last = effectiveLast;
  if (options.metricType) request.options.metricType = options.metricType;
  if (options.maxValue) request.options.maxValue = options.maxValue;
  if (options.showPercentage !== undefined)
    request.options.showPercentage = options.showPercentage;
  if (options.dimensionLimit)
    request.options.dimensionLimit = options.dimensionLimit;
  if (options.segmentLimit)
    request.options.segmentLimit = options.segmentLimit;
  if (options.rowLimit) request.options.rowLimit = options.rowLimit;
  if (options.columnLimit) request.options.columnLimit = options.columnLimit;

  // Forward comparison filters for percentage metric queries
  if (
    queryConfig.comparisonFilters &&
    queryConfig.comparisonFilters.length > 0
  ) {
    request.comparisonFilters = normalizeFilterList(
      queryConfig.comparisonFilters,
    );
  }

  return request as ChartQueryConfig;
}

// ---------------------------------------------------------------------------
// Response transformation
// ---------------------------------------------------------------------------

/** Shape of the API response data passed into the transform. */
export interface ApiResponseData {
  queryType: string;
  data: Record<string, any>[];
  metadata?: Record<string, any>;
}

/** Minimal chart descriptor needed by the transform. */
export interface ChartDescriptor {
  className: string;
  name?: string;
  queryConfig?: Record<string, any>;
}

/**
 * Transform API response data into the format expected by each DotDNA
 * chart's `setData()` method.
 *
 * Every chart class has a specific data contract -- this function maps the
 * flat API rows into the correct shape.
 */
export function transformAPIDataForChart(
  apiData: Record<string, any>[],
  chart: ChartDescriptor,
): any {
  const { className, queryConfig } = chart;
  const queryType = queryConfig?.queryType;

  // Data-source labels tend to be very long — abbreviate whichever select
  // slot targets "Data Source" (matched on the encoded key).
  const isDataSource = (v: unknown): boolean => v === DATA_SOURCE_KEY;
  const dim = queryConfig?.select?.dimension;
  const seg = queryConfig?.select?.segment;
  const src = queryConfig?.select?.sourceField;
  const tgt = queryConfig?.select?.targetField;
  const fmtDim = (v: unknown) => isDataSource(dim) ? abbreviateDataSource(v) : v;
  const fmtSeg = (v: unknown) => isDataSource(seg) ? abbreviateDataSource(v) : v;
  const fmtSrc = (v: unknown) => isDataSource(src) ? abbreviateDataSource(v) : v;
  const fmtTgt = (v: unknown) => isDataSource(tgt) ? abbreviateDataSource(v) : v;

  // ---- PIE / DONUT / CIRCLE ----
  const pieChartClasses = [
    'DotDNAAnimatedPie',
    'DotDNAAnimatedDonutModern',
    'DotDNAAnimatedCircleChart',
  ];

  if (pieChartClasses.includes(className)) {
    return apiData.map((item) => {
      const baseColor = item.color || '#3B82F6';
      const darkerColor = adjustColorBrightness(baseColor, -15);
      const label = truncateLabel(fmtDim(item.label) as string, 22);

      return {
        name: label,
        value: item.percentage || 0,
        count: item.value,
        gradient: [baseColor, darkerColor],
      };
    });
  }

  // ---- RADIAL BAR ----
  if (className === 'DotDNAAnimatedRadialBar') {
    return apiData.map((item) => {
      const baseColor = item.color || '#3B82F6';
      const darkerColor = adjustColorBrightness(baseColor, -15);

      return {
        name: fmtDim(item.label),
        value: item.value,
        gradient: [baseColor, darkerColor],
      };
    });
  }

  // ---- METRIC TREND CARDS (sparkline / mini-bars) ----
  // Metric Spark / Metric Bar render a time-series: {currentValue, trend, stats}
  // (see MetricCard.js / MetricBar.js). The trend query returns single-series
  // rows {timestamp, value} sorted ascending — map them to the series the card
  // draws, the latest bucket as the headline, and first/last buckets as stats.
  if (className === 'DotDNAMetricCard' || className === 'DotDNAMetricBar') {
    const points = apiData.map((row) => ({
      label: trendBucketLabel(row),
      value: Number(row.value) || 0,
    }));

    if (points.length === 0) {
      return [{ currentValue: 0, trend: [], stats: [] }];
    }

    const first = points[0];
    const last = points[points.length - 1];
    // With a single bucket the first and last points coincide — show one stat
    // (the current bucket) instead of rendering the same value twice.
    const stats =
      points.length === 1
        ? [
            {
              label: last.label,
              value: formatMetricStat(last.value),
              highlight: true,
            },
          ]
        : [
            { label: first.label, value: formatMetricStat(first.value) },
            {
              label: last.label,
              value: formatMetricStat(last.value),
              highlight: true,
            },
          ];
    return [{ currentValue: last.value, trend: points, stats }];
  }

  // ---- GAUGE / METRIC CARD / ULTRA NUMBER ----
  const metricClasses = [
    'DotDNAElegantGauge',
    'DotDNACircleGauge',
    'DotDNALinearGauge',
    'DotDNAUltraNumber',
  ];

  if (metricClasses.includes(className)) {
    const item = apiData[0] || {};
    return [
      {
        value: item.value || 0,
        displayValue: item.displayValue,
        formattedValue: item.formattedValue,
        showPercent: item.showPercent || false,
        percentValue: item.percentValue || 0,
        label: chart.name,
      },
    ];
  }

  // ---- SEGMENTED BAR (horizontal / vertical) ----
  if (
    className === 'DotDNACleanSegmentedHorizontalBar' ||
    className === 'DotDNACleanSegmentedVerticalBar'
  ) {
    if (queryType === 'history_trend') {
      const hasSeries = apiData.some((r) => r.series != null);
      if (!hasSeries) {
        return apiData.map((r) => ({
          name: trendBucketLabel(r),
          value: r.value,
          color: r.color,
        }));
      }
      const order: string[] = [];
      const map: Record<string, Record<string, number>> = {};
      const seriesColors: Record<string, string> = {};
      apiData.forEach((r) => {
        const b = trendBucketLabel(r);
        const s = String(r.series);
        if (!map[b]) {
          map[b] = {};
          order.push(b);
        }
        map[b][s] = r.value;
        if (!seriesColors[s] && r.color) seriesColors[s] = r.color;
      });
      const seriesNames = Object.keys(seriesColors);
      return {
        mode: 'grouped',
        groups: order.map((name) => ({
          name,
          bars: seriesNames
            .map((s) => ({ label: s, value: map[name][s] || 0, color: seriesColors[s] }))
            .filter((b) => b.value > 0),
        })),
        legend: seriesNames.map((s) => ({ label: s, color: seriesColors[s] })),
      };
    }

    if (queryType === 'comparison') {
      // Pivot: group by x_axis, collect y_axis as bars per group
      const groupOrder: string[] = [];
      const groupMap: Record<string, Record<string, number>> = {};
      const segmentColors: Record<string, string> = {};

      apiData.forEach((item) => {
        const group = fmtDim(item.x_axis) as string;
        const segment = fmtSeg(item.y_axis) as string;
        if (!groupMap[group]) {
          groupMap[group] = {};
          groupOrder.push(group);
        }
        groupMap[group][segment] = item.value;
        if (!segmentColors[segment]) {
          segmentColors[segment] = item.y_color || item.x_color;
        }
      });

      const segments = Object.keys(segmentColors);
      return {
        mode: 'grouped',
        groups: groupOrder.map((name) => ({
          name,
          bars: segments
            .map((seg) => ({
              label: seg,
              value: groupMap[name][seg] || 0,
              color: segmentColors[seg],
            }))
            .filter((b) => b.value > 0),
        })),
        legend: segments.map((seg) => ({
          label: seg,
          color: segmentColors[seg],
        })),
      };
    }

    // Distribution (simple mode): {name, value, color}
    return apiData.map((item) => ({
      name: fmtDim(item.label),
      value: item.value,
      color: item.color,
    }));
  }

  // ---- BAR CHARTS (legacy animated) ----
  if (
    className === 'DotDNAAnimatedHorizontalBar' ||
    className === 'DotDNAAnimatedVerticalColumn'
  ) {
    if (queryType === 'comparison') {
      return apiData.map((item) => ({
        name: `${fmtDim(item.x_axis)} - ${fmtSeg(item.y_axis)}`,
        value: item.value,
        color: item.x_color || item.y_color,
      }));
    }
    return apiData.map((item) => ({
      name: fmtDim(item.label),
      value: item.value,
      color: item.color,
    }));
  }

  // ---- SANKEY ----
  if (className === 'DotDNAAnimatedSankey' || className === 'DotDNASankey') {
    return apiData.map((item) => ({
      source: fmtSrc(item.source),
      target: fmtTgt(item.target),
      value: item.value,
      sourceColor: item.source_color,
      targetColor: item.target_color,
    }));
  }

  // ---- PUNCHCARD ----
  if (className === 'DotDNAPunchCard') {
    const rawCats = [...new Set(apiData.map((d) => d.source as string))];
    const rawSrcs = [...new Set(apiData.map((d) => d.target as string))];
    const categories = rawCats.map((c) => fmtSrc(c) as string);
    const sources = rawSrcs.map((s) => fmtTgt(s) as string);
    const matrix = rawCats.map((cat) =>
      rawSrcs.map((src) => {
        const item = apiData.find(
          (d) => d.source === cat && d.target === src,
        );
        return item ? item.value : 0;
      }),
    );
    return { categories, sources, matrix };
  }

  // ---- STACKED COLUMN ----
  if (className === 'DotDNAStackedColumn') {
    const xAxisValues = [
      ...new Set(apiData.map((d) => (d.source || d.x_axis) as string)),
    ];
    const stackValues = [
      ...new Set(apiData.map((d) => (d.target || d.y_axis) as string)),
    ];
    return xAxisValues.map((xVal) => ({
      label: fmtDim(xVal) as string,
      segments: stackValues
        .map((stackVal) => {
          const item = apiData.find(
            (d) =>
              (d.source || d.x_axis) === xVal &&
              (d.target || d.y_axis) === stackVal,
          );
          return { value: item ? item.value : 0, label: fmtSeg(stackVal) as string };
        })
        .filter((s) => s.value > 0),
    }));
  }

  // ---- BUTTERFLY / PYRAMID ----
  if (className === 'DotDNAVerticalPyramid') {
    if (apiData[0]?.x_axis !== undefined) {
      return apiData.map((item, index) => ({
        id: index,
        label: fmtDim(item.x_axis),
        leftValue: item.left_value || item.value || 0,
        rightValue: item.right_value || 0,
      }));
    }
    return apiData.map((item, index) => ({
      id: item.id ?? index,
      label: fmtDim(item.label || item.name),
      leftValue: item.leftValue || item.left_value || 0,
      rightValue: item.rightValue || item.right_value || 0,
    }));
  }

  // ---- LINE CHART ----
  if (className === 'DotDNALineChart') {
    return apiData.map((item) => ({
      label: item.timestamp || item.period || item.label,
      value: item.value,
      series: fmtDim(item.series),
      color: item.color,
    }));
  }

  // ---- FORCE GRAPH ----
  if (className === 'DotDNAForceGraph') {
    return apiData;
  }

  // Default: return data as-is
  return apiData;
}
