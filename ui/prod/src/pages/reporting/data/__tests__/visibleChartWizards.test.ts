// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import { CHART_TYPES } from '../chartConfig';
import {
  getChartQuestions,
  generateChartQuery,
  validateChartAnswers,
  type ResolvedChartQuestions,
  type QuestionField,
} from '../chartQuestions';
import { transformAPIDataForChart } from '../chartDataPipeline';

/**
 * Wizard-completeness contract for every VISIBLE chart type (#916).
 *
 * The "Add Chart" picker shows exactly `CHART_TYPES.filter(c => !c.hidden)`
 * (see wizard/ChartWizard.tsx). This test locks in that each of those visible
 * entries has a complete, functional configuration wizard:
 *
 *   1. `getChartQuestions(className)` resolves to a real question set
 *      (sections with well-formed fields), so the editor renders a form
 *      instead of `null` (ChartEditor returns null when the config is null).
 *   2. `generateChartQuery` never throws on the seeded default answers a fresh
 *      chart starts with, and returns a valid, non-empty query with at least
 *      one addressable `select` slot once the required fields are filled in
 *      (the preview fetch in useChartPreview.ts is gated on exactly those slots).
 *   3. `validateChartAnswers` passes for that representative filled-in answer set.
 *   4. The response transform (`transformAPIDataForChart`) reshapes rows for the
 *      className — the Data Table legitimately bypasses the pipeline.
 *
 * It is a drift guard: adding a visible chart without wiring its wizard, or
 * regressing an existing one (dropped CHART_QUESTIONS entry, generateQuery that
 * yields an empty/invalid config, a transform branch removed), fails here.
 *
 * `hidden` is authoritative for scope. Hidden charts are intentionally excluded
 * from the picker and from this contract.
 */

const visibleCharts = CHART_TYPES.filter((c) => !c.hidden);

// The Data Table renders raw asset rows through its own table component
// (ChartEditor special-cases `isTable`) and legitimately falls through
// transformAPIDataForChart unchanged, so it is exempt from the transform +
// select-slot assertions below.
const TABLE_CLASSNAMES = new Set(['DotDNADataTable']);

// The primary select slots the preview fetch is gated on (useChartPreview.ts:86
// returns early unless one of these is set). A generated query for a non-table
// chart must populate at least one, or the preview never fires and the chart
// can never load data. `segment` / `targetField` are deliberately excluded:
// they are secondary slots that never define a query on their own (a comparison
// needs dimension+segment, a correlation needs sourceField+targetField), so
// they must not satisfy this check.
const SELECT_SLOTS = ['dimension', 'metric', 'timeField', 'sourceField'];

const VALID_FIELD_TYPES = new Set([
  'field-select',
  'multi-field-select',
  'choice',
  'number',
  'text',
  'toggle',
  'filter-builder',
]);

/**
 * A representative API response row wide enough that every per-class transform
 * branch has the keys it reads. The default (unhandled-className) branch of
 * transformAPIDataForChart returns the rows unchanged, so a real transform
 * always produces something structurally different from `[SAMPLE_ROW]`.
 */
const SAMPLE_ROW: Record<string, any> = {
  label: 'Windows Server',
  name: 'Windows Server',
  value: 42,
  percentage: 55,
  count: 42,
  color: '#3b82f6',
  x_axis: 'Windows',
  y_axis: 'Server',
  x_color: '#111111',
  y_color: '#222222',
  source: 'Windows',
  target: 'Server',
  source_color: '#123456',
  target_color: '#654321',
  series: 'Windows',
  timestamp: '2026-01-01T00:00:00Z',
  period: '2026-01-01',
  left_value: 10,
  right_value: 5,
  displayValue: '42',
  formattedValue: '42',
  showPercent: true,
  percentValue: 55,
  id: 3,
};

type Answers = Record<string, any>;

/** Seed the answers a freshly-added chart starts with — every field default,
 *  exactly like ChartEditor's "new chart" branch. */
function seedDefaults(config: ResolvedChartQuestions | null): Answers {
  const answers: Answers = {};
  if (!config) return answers;
  for (const section of config.sections) {
    for (const field of section.fields) {
      if (field.default !== undefined) answers[field.id] = field.default;
    }
  }
  return answers;
}

/** A representative value for a required field the user must supply. */
function representativeValue(field: QuestionField): any {
  switch (field.type) {
    case 'field-select':
      // Give two-dimension charts two distinct fields so the cross-tab is real.
      return field.id === 'secondDimension' || field.id === 'segment'
        ? 'static:Subcategory'
        : 'static:Category';
    case 'multi-field-select':
      return ['static:Category'];
    case 'choice':
      return field.default ?? field.options?.[0]?.value;
    case 'number':
      return field.default ?? field.min ?? 1;
    case 'text':
      return 'Test';
    case 'toggle':
      return field.default ?? false;
    case 'filter-builder':
      return [];
    default:
      return 'test';
  }
}

/**
 * Build a fully-answered form: start from the seeded defaults, then fill every
 * currently-visible required field. `showIf` (section- and field-level) can
 * depend on earlier answers (mode, metricType), so it is re-evaluated against
 * the answers accumulated so far — the same way the live form does.
 */
function fillRequiredAnswers(config: ResolvedChartQuestions | null): Answers {
  const answers = seedDefaults(config);
  if (!config) return answers;
  const ctx = () => ({ answers, chartMeta: config.chartMeta });
  for (const section of config.sections) {
    if (typeof section.showIf === 'function' && !section.showIf(ctx())) continue;
    for (const field of section.fields) {
      if (typeof field.showIf === 'function' && !field.showIf(ctx())) continue;
      const current = answers[field.id];
      if (
        field.required &&
        (current === undefined || current === null || current === '')
      ) {
        answers[field.id] = representativeValue(field);
      }
    }
  }
  return answers;
}

describe('visible chart wizards are complete (#916)', () => {
  it('every visible chart type has a config wizard mapping', () => {
    // Sanity: the picker has something to show, and each entry is a real config.
    expect(visibleCharts.length).toBeGreaterThan(0);
    const unmapped = visibleCharts
      .filter((c) => getChartQuestions(c.className, c.chartId) === null)
      .map((c) => `${c.name} (${c.className})`);
    expect(unmapped, `visible charts with no wizard: ${unmapped.join(', ')}`).toEqual([]);
  });

  describe.each(visibleCharts.map((c) => [c.name, c.className, c.chartId] as const))(
    '%s [%s]',
    (name, className, chartId) => {
      const config = getChartQuestions(className, chartId);

      it('resolves to a real question set with well-formed fields', () => {
        expect(config, `getChartQuestions('${className}') returned null`).not.toBeNull();
        expect(config!.sections.length).toBeGreaterThan(0);

        const fields = config!.sections.flatMap((s) => s.fields);
        expect(fields.length, `${className} wizard has no fields`).toBeGreaterThan(0);
        for (const field of fields) {
          expect(field.id, `${className} field missing id`).toBeTruthy();
          expect(field.label, `${className} field '${field.id}' missing label`).toBeTruthy();
          expect(
            VALID_FIELD_TYPES.has(field.type),
            `${className} field '${field.id}' has invalid type '${field.type}'`,
          ).toBe(true);
        }
      });

      it('generateChartQuery does not throw on the seeded default answers', () => {
        // May legitimately return null for charts that require a user-picked
        // field (two-dimension charts) — but it must never throw.
        const seeded = seedDefaults(config);
        expect(() => generateChartQuery(className, seeded, chartId)).not.toThrow();
      });

      it('generates a valid, non-empty query from representative answers', () => {
        const filled = fillRequiredAnswers(config);
        const query = generateChartQuery(className, filled, chartId);

        expect(
          query,
          `${className} generateChartQuery returned null for filled answers ${JSON.stringify(filled)}`,
        ).not.toBeNull();
        expect(typeof query!.queryType).toBe('string');
        expect(query!.queryType.length).toBeGreaterThan(0);

        if (!TABLE_CLASSNAMES.has(className)) {
          const select = (query!.select ?? {}) as Record<string, any>;
          // history_trend is valid with only options (server buckets by
          // archived_at), so it has no select slot — same exemption as
          // hasRenderableQuery in chartDataPipeline.ts.
          const hasSlot =
            query!.queryType === 'history_trend' ||
            SELECT_SLOTS.some((k) => select[k] != null);
          expect(
            hasSlot,
            `${className} query has no addressable select slot: ${JSON.stringify(select)}`,
          ).toBe(true);
        }
      });

      it('passes validation with representative answers', () => {
        const filled = fillRequiredAnswers(config);
        const { valid, errors } = validateChartAnswers(filled, className, chartId);
        expect(valid, `${className} validation errors: ${errors.join('; ')}`).toBe(true);
      });

      it('is handled by the response transform (non-table charts reshape rows)', () => {
        if (TABLE_CLASSNAMES.has(className)) return; // table bypasses the pipeline

        const filled = fillRequiredAnswers(config);
        const query = generateChartQuery(className, filled, chartId)!;
        const out = transformAPIDataForChart([SAMPLE_ROW], {
          className,
          queryConfig: query,
          name,
        });

        expect(out, `${className} transform returned nothing`).toBeDefined();
        // The unhandled-className branch returns rows unchanged. A real
        // per-class transform reshapes them, so identical JSON means the
        // className fell through the pipeline and the chart won't render.
        expect(
          JSON.stringify(out),
          `${className} is not handled by transformAPIDataForChart (falls through to the default branch)`,
        ).not.toEqual(JSON.stringify([SAMPLE_ROW]));
      });
    },
  );
});
