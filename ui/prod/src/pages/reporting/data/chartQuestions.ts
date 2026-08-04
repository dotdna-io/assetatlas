// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Chart Questions Configuration
 *
 * Unified template system: templates define shared structure,
 * ChartQuestions maps each chart to a template with per-chart overrides.
 *
 * Architecture:
 *   1. ConfigTemplates  - 7 template definitions (distribution, barChart, metric, twoDimension, trend, forceGraph, table)
 *   2. CHART_QUESTIONS  - Maps 16 visible chart classNames to templates + overrides
 *   3. Public API       - getChartQuestions, validateChartAnswers
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface QuestionField {
  id: string;
  label: string;
  type:
    | 'field-select'
    | 'multi-field-select'
    | 'choice'
    | 'number'
    | 'text'
    | 'toggle'
    | 'filter-builder';
  required?: boolean;
  default?: any;
  placeholder?: string;
  fieldTypes?: string[];
  options?: { value: string | number; label: string }[];
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
  showIf?: (ctx: {
    answers: Record<string, any>;
    chartMeta: Record<string, any>;
  }) => boolean;
  showHintIf?: (ctx: {
    answers: Record<string, any>;
    chartMeta: Record<string, any>;
  }) => boolean;
}

export interface QuestionSection {
  title: string;
  optional?: boolean;
  collapsible?: boolean;
  showIf?: (ctx: {
    answers: Record<string, any>;
    chartMeta: Record<string, any>;
  }) => boolean;
  fields: QuestionField[];
}

export interface ChartQuestionTemplate {
  sections: QuestionSection[];
  generateQuery:
    | ((answers: Record<string, any>) => Record<string, any> | null)
    | null;
}

export interface ResolvedChartQuestions {
  name: string;
  template: string;
  description: string;
  sections: QuestionSection[];
  chartMeta: Record<string, any>;
  generateQuery:
    | ((answers: Record<string, any>) => Record<string, any> | null)
    | null;
}

interface ChartQuestionDef {
  name: string;
  template: string;
  description: string;
  overrides?: Record<string, Partial<QuestionField>>;
  chartMeta?: Record<string, any>;
  extraFields?: QuestionField[];
  generateQuery?: (
    answers: Record<string, any>,
  ) => Record<string, any> | null;
}

// ---------------------------------------------------------------------------
// Part 1: ConfigTemplates
// ---------------------------------------------------------------------------

const ConfigTemplates: Record<string, ChartQuestionTemplate> = {
  // ----- distribution -----
  distribution: {
    sections: [
      {
        title: 'Data',
        fields: [
          {
            id: 'dimension',
            label: 'Dimension',
            type: 'field-select',
            required: true,
            fieldTypes: ['string', 'string_array'],
            placeholder: 'Select a field...',
          },
          {
            id: 'limit',
            label: 'Max items',
            type: 'number',
            required: false,
            default: 10,
            min: 3,
            max: 20,
          },
          {
            id: 'sortOrder',
            label: 'Sort order',
            type: 'choice',
            required: false,
            default: 'desc',
            showIf: (ctx) => ctx.chartMeta.showSort,
            options: [
              { value: 'desc', label: 'Highest first' },
              { value: 'asc', label: 'Lowest first' },
            ],
          },
          {
            id: 'includeEmpty',
            label: 'Include empty values',
            type: 'toggle',
            required: false,
            default: false,
          },
        ],
      },
      {
        title: 'Filters',
        optional: true,
        fields: [
          {
            id: 'filters',
            label: 'Filter assets',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
    ],
    generateQuery: (answers) => ({
      queryType: 'distribution',
      from: { table: 'assets' },
      select: { dimension: answers.dimension },
      options: {
        limit: answers.limit || 10,
        sortOrder: answers.sortOrder || 'desc',
        includeOther: true,
        includeNull: answers.includeEmpty || false,
      },
      filters: answers.filters || [],
    }),
  },

  // ----- barChart -----
  barChart: {
    sections: [
      {
        title: 'Chart Mode',
        fields: [
          {
            id: 'mode',
            label: 'Mode',
            type: 'choice',
            required: true,
            default: 'simple',
            options: [
              { value: 'simple', label: 'Simple' },
              { value: 'grouped', label: 'Grouped' },
            ],
          },
        ],
      },
      {
        title: 'Data',
        fields: [
          {
            id: 'dimension',
            label: 'Dimension',
            type: 'field-select',
            required: true,
            fieldTypes: ['string', 'string_array'],
            placeholder: 'Select a field...',
          },
          {
            id: 'segment',
            label: 'Compare by',
            type: 'field-select',
            required: true,
            fieldTypes: ['string', 'string_array'],
            placeholder: 'Select a field...',
            showIf: (ctx) => ctx.answers.mode === 'grouped',
          },
          {
            id: 'limit',
            label: 'Max groups',
            type: 'number',
            required: false,
            default: 10,
            min: 3,
            max: 20,
          },
          {
            id: 'segmentLimit',
            label: 'Max bars per group',
            type: 'number',
            required: false,
            default: 5,
            min: 2,
            max: 10,
            showIf: (ctx) => ctx.answers.mode === 'grouped',
          },
          {
            id: 'sortOrder',
            label: 'Sort order',
            type: 'choice',
            required: false,
            default: 'desc',
            showIf: (ctx) =>
              ctx.answers.mode === 'simple' && ctx.chartMeta.showSort,
            options: [
              { value: 'desc', label: 'Highest first' },
              { value: 'asc', label: 'Lowest first' },
            ],
          },
          {
            id: 'includeEmpty',
            label: 'Include empty values',
            type: 'toggle',
            required: false,
            default: false,
          },
        ],
      },
      {
        title: 'Filters',
        optional: true,
        fields: [
          {
            id: 'filters',
            label: 'Filter assets',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
    ],
    generateQuery: (answers) => {
      if (answers.mode === 'grouped') {
        if (!answers.dimension || !answers.segment) return null;
        return {
          queryType: 'comparison',
          from: { table: 'assets' },
          select: {
            dimension: answers.dimension,
            segment: answers.segment,
          },
          options: {
            limit: answers.limit || 10,
            segmentLimit: answers.segmentLimit || 5,
            includeOther: true,
            includeNull: answers.includeEmpty || false,
          },
          filters: answers.filters || [],
        };
      }
      return {
        queryType: 'distribution',
        from: { table: 'assets' },
        select: { dimension: answers.dimension },
        options: {
          limit: answers.limit || 10,
          sortOrder: answers.sortOrder || 'desc',
          includeOther: true,
          includeNull: answers.includeEmpty || false,
        },
        filters: answers.filters || [],
      };
    },
  },

  // ----- metric -----
  metric: {
    sections: [
      {
        title: 'Metric',
        fields: [
          {
            id: 'metricType',
            label: 'Metric type',
            type: 'choice',
            required: true,
            options: [
              { value: 'total', label: 'Total asset count' },
              { value: 'filtered', label: 'Assets matching a filter' },
              {
                value: 'percentage',
                label: 'Percentage of assets matching',
              },
            ],
          },
        ],
      },
      {
        title: 'Field',
        showIf: (ctx) => ctx.answers.metricType === 'distinct',
        fields: [
          {
            id: 'distinctField',
            label: 'Count unique values of',
            type: 'field-select',
            required: true,
          },
        ],
      },
      {
        title: 'Base set',
        showIf: (ctx) => ctx.answers.metricType === 'percentage',
        fields: [
          {
            id: 'comparisonFilters',
            label: 'Which assets form the total?',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
      {
        title: 'Count',
        showIf: (ctx) => ctx.answers.metricType !== 'total',
        fields: [
          {
            id: 'filters',
            label: 'Which assets to count?',
            type: 'filter-builder',
            required: false,
            hint: 'Base set filters are applied automatically',
            showHintIf: (ctx) => ctx.answers.metricType === 'percentage',
          },
        ],
      },
      {
        title: 'Thresholds',
        collapsible: true,
        showIf: (ctx) => ctx.answers.metricType === 'percentage',
        fields: [
          {
            id: 'thresholdLow',
            label: 'Yellow above (%)',
            type: 'number',
            required: false,
            default: 33,
            min: 1,
            max: 99,
          },
          {
            id: 'thresholdHigh',
            label: 'Green above (%)',
            type: 'number',
            required: false,
            default: 66,
            min: 1,
            max: 99,
          },
          {
            id: 'invertColors',
            label: 'Invert colors (red = high)',
            type: 'toggle',
            required: false,
            default: false,
          },
          {
            id: 'dashedSegments',
            label: 'Use dashed segments',
            type: 'toggle',
            required: false,
            default: false,
          },
        ],
      },
    ],
    generateQuery: (answers) => {
      // Backend's MetricSpec is `{agg, field_id?}`. The SQL builder ignores
      // it for the 'percentage' and 'distinct' branches (those use
      // comparisonFilters / select.dimension), but Pydantic still requires
      // a valid value — so always emit `{agg: 'COUNT'}`.
      const select: Record<string, any> = { metric: { agg: 'COUNT' } };
      if (answers.metricType === 'distinct' && answers.distinctField) {
        select.dimension = answers.distinctField;
      }
      return {
        queryType: 'metric',
        from: { table: 'assets' },
        select,
        options: {
          metricType: answers.metricType,
          showPercentage: answers.metricType === 'percentage',
          maxValue: 100,
          thresholdLow: answers.thresholdLow ?? 33,
          thresholdHigh: answers.thresholdHigh ?? 66,
          invertColors: answers.invertColors ?? false,
          dashedSegments: answers.dashedSegments ?? false,
        },
        filters: answers.filters || [],
        comparisonFilters: answers.comparisonFilters || [],
      };
    },
  },

  // ----- twoDimension -----
  twoDimension: {
    sections: [
      {
        title: 'Data',
        fields: [
          {
            id: 'dimension',
            label: 'Primary dimension',
            type: 'field-select',
            required: true,
            fieldTypes: ['string', 'string_array'],
            placeholder: 'Select a field...',
          },
          {
            id: 'secondDimension',
            label: 'Second dimension',
            type: 'field-select',
            required: true,
            fieldTypes: ['string', 'string_array'],
            placeholder: 'Select a field...',
          },
          {
            id: 'limit',
            label: 'Max primary items',
            type: 'number',
            required: false,
            default: 8,
            min: 3,
            max: 15,
          },
          {
            id: 'secondLimit',
            label: 'Max secondary items',
            type: 'number',
            required: false,
            default: 5,
            min: 2,
            max: 10,
          },
        ],
      },
      {
        title: 'Filters',
        optional: true,
        fields: [
          {
            id: 'filters',
            label: 'Filter assets',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
    ],
    generateQuery: null, // overridden per chart
  },

  // ----- trend -----
  trend: {
    sections: [
      {
        title: 'Metric',
        fields: [
          {
            id: 'metric',
            label: 'What to track',
            type: 'choice',
            required: true,
            default: 'count',
            options: [
              { value: 'count', label: 'Asset count over time' },
              { value: 'percentage', label: 'Percentage over time' },
            ],
          },
          {
            id: 'dimension',
            label: 'Split by',
            type: 'field-select',
            required: false,
            placeholder: 'None (total count)',
            fieldTypes: ['string', 'string_array'],
          },
          {
            id: 'dimensionLimit',
            label: 'Max lines',
            type: 'number',
            required: false,
            showIf: (ctx) => !!ctx.answers.dimension,
            default: 5,
            min: 2,
            max: 10,
          },
        ],
      },
      {
        title: 'Base set',
        showIf: (ctx) => ctx.answers.metric === 'percentage',
        fields: [
          {
            id: 'comparisonFilters',
            label: 'Which assets form the total?',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
      {
        title: 'Count',
        showIf: (ctx) => ctx.answers.metric === 'percentage',
        fields: [
          {
            id: 'filters',
            label: 'Which assets to count?',
            type: 'filter-builder',
            required: false,
            hint: 'Base set filters are applied automatically',
            showHintIf: (ctx) => ctx.answers.metric === 'percentage',
          },
        ],
      },
      {
        title: 'Time Range',
        fields: [
          {
            id: 'timeRange',
            label: 'Time period',
            type: 'choice',
            required: true,
            default: '30d',
            options: [
              { value: '7d', label: 'Last 7 days' },
              { value: '30d', label: 'Last 30 days' },
              { value: '90d', label: 'Last 90 days' },
              { value: '180d', label: 'Last 6 months' },
              { value: '1y', label: 'Last year' },
              { value: 'all', label: 'All time' },
            ],
          },
          {
            id: 'interval',
            label: 'Group by',
            type: 'choice',
            required: true,
            default: 'day',
            options: [
              { value: 'day', label: 'Day' },
              { value: 'week', label: 'Week' },
              { value: 'month', label: 'Month' },
            ],
          },
        ],
      },
      {
        title: 'Filters',
        optional: true,
        showIf: (ctx) => ctx.answers.metric === 'count',
        fields: [
          {
            id: 'filters',
            label: 'Filter assets',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
    ],
    generateQuery: (answers) => {
      const isPercentage = answers.metric === 'percentage';
      const hasDimension = !!answers.dimension;

      // Snapshot history over assets_history, bucketed server-side by
      // archived_at (+ a live "now" point). No timeField (#497/#946).
      const query: Record<string, any> = {
        queryType: 'history_trend',
        from: { table: 'assets' },
        select: {},
        options: {
          timeRange:
            answers.timeRange !== 'all' ? answers.timeRange : undefined,
          interval: answers.interval,
          dimensionLimit: answers.dimensionLimit || 5,
          showPercentage: isPercentage,
        },
        filters: answers.filters || [],
      };

      if (hasDimension) {
        query.select.dimension = answers.dimension;
      }

      if (isPercentage) {
        query.comparisonFilters = answers.comparisonFilters || [];
      }

      return query;
    },
  },

  // ----- metricTrend -----
  // Trend-driven variant of the metric cards (Metric Spark / Metric Bar).
  // Same headline number as the plain `metric` template, but backed by a
  // time-series so the sparkline / mini-bars + first/last stats actually
  // render instead of collapsing to a bare number (#946). The transform for
  // these two classes lives in chartDataPipeline.ts.
  metricTrend: {
    sections: [
      {
        title: 'Time Range',
        fields: [
          {
            id: 'timeRange',
            label: 'Time period',
            type: 'choice',
            required: true,
            default: '30d',
            options: [
              { value: '7d', label: 'Last 7 days' },
              { value: '30d', label: 'Last 30 days' },
              { value: '90d', label: 'Last 90 days' },
              { value: '180d', label: 'Last 6 months' },
              { value: '1y', label: 'Last year' },
              { value: 'all', label: 'All time' },
            ],
          },
          {
            id: 'interval',
            label: 'Group by',
            type: 'choice',
            required: true,
            default: 'day',
            options: [
              { value: 'day', label: 'Day' },
              { value: 'week', label: 'Week' },
              { value: 'month', label: 'Month' },
            ],
          },
        ],
      },
      {
        title: 'Filters',
        optional: true,
        fields: [
          {
            id: 'filters',
            label: 'Filter assets',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
    ],
    generateQuery: (answers) => ({
      // Snapshot history over assets_history, bucketed server-side by
      // archived_at (+ a live "now" point). No timeField (#497/#946). Single
      // series only: the cards render one sparkline / mini-bar row, so there
      // is no split-by dimension.
      queryType: 'history_trend',
      from: { table: 'assets' },
      select: {},
      options: {
        timeRange:
          answers.timeRange !== 'all' ? answers.timeRange : undefined,
        interval: answers.interval,
      },
      filters: answers.filters || [],
    }),
  },

  // ----- forceGraph -----
  forceGraph: {
    sections: [
      {
        title: 'Drill-Down Levels',
        fields: [
          {
            id: 'level1',
            label: 'Level 1 (starting)',
            type: 'field-select',
            required: true,
            default: 'static:Category',
            fieldTypes: ['string', 'string_array'],
          },
          {
            id: 'level2',
            label: 'Level 2',
            type: 'field-select',
            required: true,
            default: 'static:OS%20Normalized',
            fieldTypes: ['string', 'string_array'],
          },
          {
            id: 'level3',
            label: 'Level 3 (-> Explorer)',
            type: 'field-select',
            required: true,
            default: 'static:Data%20Source',
            fieldTypes: ['string', 'string_array'],
          },
        ],
      },
      {
        title: 'Filters',
        optional: true,
        fields: [
          {
            id: 'filters',
            label: 'Filter assets',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
    ],
    generateQuery: (answers) => ({
      queryType: 'distribution',
      from: { table: 'assets' },
      select: { dimension: answers.level1 || 'static:Category' },
      options: {
        limit: 12,
        sortOrder: 'desc',
        includeOther: true,
        includeNull: false,
      },
      filters: answers.filters || [],
      level1: answers.level1 || 'static:Category',
      level2: answers.level2 || 'static:OS%20Normalized',
      level3: answers.level3 || 'static:Data%20Source',
    }),
  },

  // ----- table -----
  table: {
    sections: [
      {
        title: 'Columns',
        fields: [
          {
            id: 'columns',
            label: 'Visible columns',
            type: 'multi-field-select',
            required: false,
            fieldTypes: [
              'string',
              'string_array',
              'timestamp',
              'boolean',
              'float',
              'numeric',
            ],
            placeholder:
              'Default: hostname, ip, category, subcategory, os, mac, data_source, last_seen, status',
          },
          {
            id: 'itemsPerPage',
            label: 'Rows per page',
            type: 'choice',
            required: false,
            default: 25,
            options: [
              { value: 10, label: '10 rows' },
              { value: 25, label: '25 rows' },
              { value: 50, label: '50 rows' },
            ],
          },
        ],
      },
      {
        title: 'Filters',
        optional: true,
        fields: [
          {
            id: 'filters',
            label: 'Filter assets',
            type: 'filter-builder',
            required: false,
          },
        ],
      },
    ],
    generateQuery: (answers) => ({
      queryType: 'table',
      options: {
        columns: answers.columns || null,
        itemsPerPage: answers.itemsPerPage || 25,
      },
      filters: answers.filters || [],
    }),
  },
};

// ---------------------------------------------------------------------------
// Part 2: ChartQuestions -- per-chart mappings to templates
// ---------------------------------------------------------------------------

/** Shared override for metric cards that show count or distinct values */
const COUNTER_METRIC_OVERRIDES: Record<string, Partial<QuestionField>> = {
  metricType: {
    default: 'filtered',
    options: [
      { value: 'filtered', label: 'Asset count' },
      { value: 'distinct', label: 'Unique values of a field' },
    ],
  },
};

const CHART_QUESTIONS: Record<string, ChartQuestionDef> = {
  // --- Distribution charts ---

  DotDNAAnimatedDonutModern: {
    name: 'Donut Chart',
    template: 'distribution',
    description: 'Show proportional breakdown with center metric',
    overrides: {
      limit: { label: 'Max slices', default: 8, max: 12 },
    },
    chartMeta: { showSort: false },
  },

  DotDNAAnimatedPie: {
    name: 'Pie Chart',
    template: 'distribution',
    description: 'Classic proportional breakdown',
    overrides: {
      limit: { label: 'Max slices', default: 8, max: 12 },
    },
    chartMeta: { showSort: false },
  },

  DotDNACleanSegmentedHorizontalBar: {
    name: 'Horizontal Bar Chart',
    template: 'barChart',
    description: 'Horizontal bars for ranking categories',
    overrides: {
      limit: { label: 'Max bars', default: 10, min: 5, max: 20 },
    },
    chartMeta: { showSort: true },
  },

  DotDNACleanSegmentedVerticalBar: {
    name: 'Vertical Bar Chart',
    template: 'barChart',
    description: 'Vertical bars for comparing categories',
    overrides: {
      limit: { label: 'Max bars', default: 8, min: 3, max: 15 },
    },
    chartMeta: { showSort: true },
  },

  // --- Metric charts ---

  DotDNAElegantGauge: {
    name: 'Gauge',
    template: 'metric',
    description: 'Show percentage or progress toward a goal',
    overrides: {
      metricType: {
        default: 'percentage',
        options: [
          { value: 'percentage', label: 'Percentage of assets matching' },
        ],
      },
    },
  },

  DotDNACircleGauge: {
    name: 'Circle Gauge',
    template: 'metric',
    description: 'Circular gauge displaying a metric',
    overrides: {
      metricType: {
        default: 'percentage',
        options: [
          { value: 'percentage', label: 'Percentage of assets matching' },
        ],
      },
    },
  },

  DotDNALinearGauge: {
    name: 'Linear Gauge',
    template: 'metric',
    description: 'Linear progress bar displaying a metric',
    overrides: {
      metricType: {
        default: 'percentage',
        options: [
          { value: 'percentage', label: 'Percentage of assets matching' },
        ],
      },
    },
  },

  DotDNAUltraNumber: {
    name: 'Metric Card',
    template: 'metric',
    description: 'Display a single metric prominently',
    overrides: COUNTER_METRIC_OVERRIDES,
  },

  DotDNAMetricCard: {
    name: 'Metric Card (Compact)',
    template: 'metricTrend',
    description: 'Compact card with sparkline trend and stats',
    // No overrides: metricTrend has no metricType field. These cards render a
    // time-series (sparkline + stats), not a single filtered/distinct count.
  },

  DotDNAMetricBar: {
    name: 'Metric Bar',
    template: 'metricTrend',
    description: 'Horizontal bar with mini bar-chart trend and stats',
  },

  // --- Two-dimension charts ---

  DotDNAStackedColumn: {
    name: 'Stacked Column Chart',
    template: 'twoDimension',
    description: 'Compare categories with stacked segments',
    overrides: {
      dimension: { label: 'Dimension' },
      secondDimension: { label: 'Compare by' },
      limit: { label: 'Max groups', default: 8 },
      secondLimit: {
        label: 'Max bars per group',
        default: 5,
        min: 2,
        max: 8,
      },
    },
    extraFields: [
      {
        id: 'normalize',
        label: 'Normalize to 100%',
        type: 'toggle',
        required: false,
        default: false,
      },
    ],
    generateQuery: (answers) => {
      if (!answers.dimension || !answers.secondDimension) return null;
      return {
        queryType: 'comparison',
        from: { table: 'assets' },
        select: {
          dimension: answers.dimension,
          segment: answers.secondDimension,
        },
        options: {
          limit: answers.limit || 8,
          segmentLimit: answers.secondLimit || 5,
          normalize: answers.normalize || false,
          includeOther: true,
        },
        filters: answers.filters || [],
      };
    },
  },

  DotDNAPunchCard: {
    name: 'Punchcard Matrix',
    template: 'twoDimension',
    description: 'Matrix showing intersections of two dimensions',
    overrides: {
      dimension: { label: 'Dimension' },
      secondDimension: { label: 'Compare by' },
      limit: { label: 'Max rows' },
      secondLimit: { label: 'Max columns', default: 8 },
    },
    generateQuery: (answers) => {
      if (!answers.dimension || !answers.secondDimension) return null;
      return {
        queryType: 'correlation',
        from: { table: 'assets' },
        select: {
          sourceField: answers.dimension,
          targetField: answers.secondDimension,
        },
        options: {
          rowLimit: answers.limit || 8,
          columnLimit: answers.secondLimit || 8,
        },
        filters: answers.filters || [],
      };
    },
  },

  // --- Trend charts ---

  DotDNALineChart: {
    name: 'Line Chart',
    template: 'trend',
    description: 'Track metrics over time',
  },

  // --- Table ---

  DotDNADataTable: {
    name: 'Data Table',
    template: 'table',
    description: 'Interactive asset table with search, sort, and pivot',
  },

  // --- Exploration ---

  DotDNAForceGraph: {
    name: 'Force Graph',
    template: 'forceGraph',
    description:
      'Interactive force-directed graph with drill-down exploration',
  },
};

/**
 * chartId → ChartQuestionDef overrides, consulted BEFORE the className map.
 * Needed where two catalog entries share a className but need different
 * wizards — today only Chronicle Bar (historical) vs Vertical Bar (live),
 * which both render as DotDNACleanSegmentedVerticalBar.
 */
const CHART_QUESTIONS_BY_ID: Record<string, ChartQuestionDef> = {
  'a1b2c3d4-chr1-4e5f-9a8b-vertical-bar01': {
    name: 'Chronicle Bar Chart',
    template: 'trend',
    description: 'Historical asset counts over time',
  },
};

// ---------------------------------------------------------------------------
// Part 3: Helpers
// ---------------------------------------------------------------------------

type ShowIfFn = (ctx: {
  answers: Record<string, any>;
  chartMeta: Record<string, any>;
}) => boolean;

/**
 * Collect all showIf / showHintIf functions from template sections before
 * deep-cloning (JSON round-trip drops functions).
 */
function collectShowIfs(
  sections: QuestionSection[],
): Map<string, ShowIfFn> {
  const map = new Map<string, ShowIfFn>();
  for (let i = 0; i < sections.length; i++) {
    const section = sections[i];
    if (typeof section.showIf === 'function') {
      map.set(`__section_${i}`, section.showIf);
    }
    for (const field of section.fields) {
      if (typeof field.showIf === 'function') {
        map.set(field.id, field.showIf);
      }
      if (typeof field.showHintIf === 'function') {
        map.set(`__hint_${field.id}`, field.showHintIf);
      }
    }
  }
  return map;
}

/**
 * Deep-clone sections (via JSON round-trip) then restore showIf functions.
 */
function cloneSections(sections: QuestionSection[]): QuestionSection[] {
  const showIfMap = collectShowIfs(sections);
  const cloned: QuestionSection[] = JSON.parse(JSON.stringify(sections));

  for (let i = 0; i < cloned.length; i++) {
    const section = cloned[i];
    const sectionKey = `__section_${i}`;
    if (showIfMap.has(sectionKey)) {
      section.showIf = showIfMap.get(sectionKey)!;
    }
    for (const field of section.fields) {
      if (showIfMap.has(field.id)) {
        field.showIf = showIfMap.get(field.id)!;
      }
      const hintKey = `__hint_${field.id}`;
      if (showIfMap.has(hintKey)) {
        field.showHintIf = showIfMap.get(hintKey)!;
      }
    }
  }

  return cloned;
}

// ---------------------------------------------------------------------------
// Part 4: Public API
// ---------------------------------------------------------------------------

/**
 * Get resolved chart configuration for a className.
 *
 * 1. Looks up chartDef in CHART_QUESTIONS
 * 2. Looks up template in ConfigTemplates
 * 3. Deep clones template sections (preserving showIf functions)
 * 4. Applies chartDef.overrides to matching fields
 * 5. Appends extraFields to first section
 * 6. Returns merged config object
 */
export function getChartQuestions(
  className: string,
  chartId?: string,
): ResolvedChartQuestions | null {
  const chartDef = (chartId && CHART_QUESTIONS_BY_ID[chartId]) || CHART_QUESTIONS[className];
  if (!chartDef) return null;

  const template = ConfigTemplates[chartDef.template];
  if (!template) return null;

  const sections = cloneSections(template.sections);

  // Apply per-chart overrides
  if (chartDef.overrides) {
    for (const section of sections) {
      for (const field of section.fields) {
        const override = chartDef.overrides[field.id];
        if (override) {
          Object.assign(field, override);
        }
      }
    }
  }

  // Append extra fields from chart definition to the first (Data) section
  if (chartDef.extraFields && sections.length > 0) {
    sections[0].fields.push(...chartDef.extraFields);
  }

  // Chart-level generateQuery overrides template-level
  const generateQuery = chartDef.generateQuery || template.generateQuery;

  return {
    name: chartDef.name,
    template: chartDef.template,
    description: chartDef.description,
    sections,
    chartMeta: chartDef.chartMeta || {},
    generateQuery,
  };
}

/**
 * Generate API query from user answers for a chart type.
 */
export function generateChartQuery(
  className: string,
  answers: Record<string, any>,
  chartId?: string,
): Record<string, any> | null {
  const config = getChartQuestions(className, chartId);
  if (!config || !config.generateQuery) {
    console.error('No query generator for:', className);
    return null;
  }
  return config.generateQuery(answers);
}

/**
 * Validate answers for a chart configuration.
 * Skips fields hidden by showIf. Checks required fields have values.
 */
export function validateChartAnswers(
  answers: Record<string, any>,
  className: string,
  chartId?: string,
): { valid: boolean; errors: string[] } {
  const config = getChartQuestions(className, chartId);
  if (!config) {
    return { valid: false, errors: ['Unknown chart type'] };
  }

  const errors: string[] = [];
  const ctx = { answers, chartMeta: config.chartMeta };

  for (const section of config.sections) {
    // Skip hidden sections
    if (typeof section.showIf === 'function' && !section.showIf(ctx)) {
      continue;
    }
    for (const field of section.fields) {
      // Skip hidden fields
      if (typeof field.showIf === 'function' && !field.showIf(ctx)) {
        continue;
      }

      // Check required (allow 0 and false as valid values)
      if (
        field.required &&
        (answers[field.id] === undefined ||
          answers[field.id] === null ||
          answers[field.id] === '')
      ) {
        errors.push(`${field.label} is required`);
      }

      // Check number constraints
      if (field.type === 'number' && answers[field.id] !== undefined) {
        const value = answers[field.id];
        if (field.min !== undefined && value < field.min) {
          errors.push(`${field.label} must be at least ${field.min}`);
        }
        if (field.max !== undefined && value > field.max) {
          errors.push(`${field.label} must be at most ${field.max}`);
        }
      }
    }
  }

  return { valid: errors.length === 0, errors };
}
