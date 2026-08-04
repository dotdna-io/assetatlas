// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { DotDNAChart } from '@/components/charts/DotDNAChart';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge/DataSourceBadge';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { QuestionRenderer } from './QuestionRenderer';
import { useChartPreview } from './useChartPreview';
import { useDebouncedValue } from './useDebouncedValue';
import { ReportTable, DEFAULT_COLUMNS } from '../grid/ReportTable';
import {
  getChartQuestions,
  validateChartAnswers,
  generateChartQuery,
} from '../data/chartQuestions';
import { getChartTypeByClass } from '../data/chartConfig';
import type { ChartConfig } from '../grid/ChartCard';
import styles from './ChartEditor.module.css';

import type { FieldId } from '@/types/field-id';

// Stable reference for the "no filters yet" case. Passing a fresh `[]` literal
// into useDebouncedValue on every render would flip its effect dependency each
// time, firing setDebounced with a new array every 500ms — a perpetual
// re-render loop whenever answers.filters is undefined.
const EMPTY_FILTERS: never[] = [];

interface ChartEditorProps {
  isOpen: boolean;
  onClose: () => void;
  chart: ChartConfig | null;
  assetFields: {
    name: string;
    value: string;
    field_id: FieldId;
    type: string;
    category: string;
  }[];
  onSave: (queryConfig: Record<string, any>, name: string) => void;
}

// ---------------------------------------------------------------------------
// Helpers: restore answers from a saved queryConfig
// ---------------------------------------------------------------------------

/**
 * Map from queryConfig paths back to answer keys so that re-opening the
 * editor restores the form state the user previously saved.
 */
function restoreAnswersFromConfig(
  queryConfig: Record<string, any>,
): Record<string, any> {
  const answers: Record<string, any> = {};
  const { select = {}, options = {} } = queryConfig;

  // Dimension / metric / segment
  if (select.dimension) answers.dimension = select.dimension;
  if (select.metric) answers.metric = select.metric;
  if (select.segment) {
    answers.segment = select.segment;
    // twoDimension template uses 'secondDimension' for the segment field
    answers.secondDimension = select.segment;
  }

  // Two-dimension charts (punchcard) store fields as sourceField/targetField
  if (select.sourceField) answers.dimension = select.sourceField;
  if (select.targetField) answers.secondDimension = select.targetField;

  // Options
  if (options.limit !== undefined) answers.limit = options.limit;
  if (options.sortOrder) answers.sortOrder = options.sortOrder;
  if (options.includeNull !== undefined)
    answers.includeEmpty = options.includeNull;
  if (options.metricType) answers.metricType = options.metricType;
  if (options.interval) answers.interval = options.interval;
  if (options.timeRange) answers.timeRange = options.timeRange;
  if (options.dimensionLimit) answers.dimensionLimit = options.dimensionLimit;
  if (options.segmentLimit) {
    answers.segmentLimit = options.segmentLimit;
    answers.secondLimit = options.segmentLimit;
  }
  if (options.rowLimit) answers.limit = options.rowLimit;
  if (options.columnLimit) answers.secondLimit = options.columnLimit;
  if (options.showPercentage !== undefined)
    answers.showPercentage = options.showPercentage;
  if (options.thresholdLow !== undefined)
    answers.thresholdLow = options.thresholdLow;
  if (options.thresholdHigh !== undefined)
    answers.thresholdHigh = options.thresholdHigh;
  if (options.invertColors !== undefined)
    answers.invertColors = options.invertColors;
  if (options.dashedSegments !== undefined)
    answers.dashedSegments = options.dashedSegments;
  if (options.normalize !== undefined) answers.normalize = options.normalize;
  if (options.columns) answers.columns = options.columns;
  if (options.itemsPerPage) answers.itemsPerPage = options.itemsPerPage;
  if (options.last) answers.timeRange = options.last;

  // Filters
  if (queryConfig.filters?.length) answers.filters = queryConfig.filters;
  if (queryConfig.comparisonFilters?.length)
    answers.comparisonFilters = queryConfig.comparisonFilters;

  // Force graph levels
  if (queryConfig.level1) answers.level1 = queryConfig.level1;
  if (queryConfig.level2) answers.level2 = queryConfig.level2;
  if (queryConfig.level3) answers.level3 = queryConfig.level3;

  // Bar chart mode detection
  if (queryConfig.queryType === 'comparison' && select.segment) {
    answers.mode = 'grouped';
  } else if (queryConfig.queryType === 'distribution' && !answers.mode) {
    answers.mode = 'simple';
  }

  // Trend metric type detection
  if (queryConfig.queryType === 'trend_percentage') {
    answers.metric = 'percentage';
  } else if (queryConfig.queryType === 'trend' || queryConfig.queryType === 'history_trend') {
    answers.metric = queryConfig.options?.showPercentage ? 'percentage' : 'count';
  }

  return answers;
}

// ---------------------------------------------------------------------------
// DataTable: group fields by data source for column selector
// ---------------------------------------------------------------------------

type AssetField = {
  name: string;
  value: string;
  field_id: FieldId;
  type: string;
  category: string;
};

function groupFieldsBySource(fields: AssetField[]): { source: string; fields: AssetField[] }[] {
  const groups = new Map<string, AssetField[]>();

  for (const field of fields) {
    let source = 'AssetAtlas';
    if (field.category === 'Tags') {
      source = 'Tags';
    } else if (field.category === 'Extended Attributes') {
      // Derive source from the typed FieldId (extended_attribute.source or
      // integration_group.group) rather than from a legacy JSON-path regex.
      if (field.field_id.kind === 'extended_attribute') {
        source = field.field_id.source;
      } else if (field.field_id.kind === 'integration_group') {
        source = field.field_id.group;
      } else {
        source = 'Other';
      }
    }
    if (!groups.has(source)) groups.set(source, []);
    groups.get(source)!.push(field);
  }

  // Order: AssetAtlas first, then alphabetical, Tags last
  const ordered: { source: string; fields: AssetField[] }[] = [];
  if (groups.has('AssetAtlas')) {
    ordered.push({ source: 'AssetAtlas', fields: groups.get('AssetAtlas')! });
    groups.delete('AssetAtlas');
  }
  const tagsGroup = groups.get('Tags');
  groups.delete('Tags');
  for (const [src, items] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    ordered.push({ source: src, fields: items });
  }
  if (tagsGroup) ordered.push({ source: 'Tags', fields: tagsGroup });
  return ordered;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ChartEditor({
  isOpen,
  onClose,
  chart,
  assetFields,
  onSave,
}: ChartEditorProps) {
  const [status, setStatus] = useInlineStatus();

  const [answers, setAnswers] = useState<Record<string, any>>({});
  const [chartName, setChartName] = useState('');
  // DataTable editor only: narrows the "Visible columns" field list by name.
  const [columnSearch, setColumnSearch] = useState('');

  // Resolve chart question config
  const questionConfig = useMemo(() => {
    if (!chart) return null;
    return getChartQuestions(chart.className, chart.chartId);
  }, [chart?.className, chart?.chartId]);

  // Restore state when modal opens / chart changes
  useEffect(() => {
    if (!isOpen || !chart) return;

    setChartName(chart.name);

    if (chart.queryConfig) {
      setAnswers(restoreAnswersFromConfig(chart.queryConfig));
    } else {
      // Seed answers with field defaults from the question template
      const defaults: Record<string, any> = {};
      if (questionConfig) {
        for (const section of questionConfig.sections) {
          for (const field of section.fields) {
            if (field.default !== undefined) {
              defaults[field.id] = field.default;
            }
          }
        }
      }
      // Data Table: ReportTable renders DEFAULT_COLUMNS when nothing is
      // selected — seed the picker with that set so the checkboxes match
      // what the table actually shows instead of starting blank.
      if (chart.className === 'DotDNADataTable') {
        defaults.columns = [...DEFAULT_COLUMNS];
      }
      setAnswers(defaults);
    }
  }, [isOpen, chart]);

  // Build live queryConfig from current answers
  const liveQueryConfig = useMemo(() => {
    if (!chart || !questionConfig?.generateQuery) return null;
    return questionConfig.generateQuery(answers);
  }, [answers, chart, questionConfig]);

  // Live preview
  const preview = useChartPreview(
    liveQueryConfig,
    { className: chart?.className ?? '' },
  );

  // Data Table preview: debounce the answers fed into <ReportTable> so a
  // filter keystroke or column toggle doesn't fire an /assets request per
  // keystroke — same 500ms cadence as useChartPreview above. Called
  // unconditionally (Rules of Hooks) even for non-table charts; unused there.
  const debouncedColumns = useDebouncedValue(
    Array.isArray(answers.columns) ? answers.columns : undefined,
    500,
  );
  const debouncedFilters = useDebouncedValue(answers.filters ?? EMPTY_FILTERS, 500);
  const debouncedItemsPerPage = useDebouncedValue(answers.itemsPerPage ?? 25, 500);

  const chartMeta = useMemo(
    () => questionConfig?.chartMeta ?? {},
    [questionConfig],
  );

  const handleAnswerChange = useCallback((key: string, value: any) => {
    setAnswers((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleSave = useCallback(() => {
    if (!chart || !questionConfig) return;

    // Validate
    const { valid, errors } = validateChartAnswers(answers, chart.className, chart.chartId);
    if (!valid) {
      setStatus({ kind: 'error', message: errors[0] || 'Please fill in required fields' });
      return;
    }

    // Generate query
    const queryConfig = generateChartQuery(chart.className, answers, chart.chartId);
    if (!queryConfig) {
      setStatus({ kind: 'error', message: 'Could not generate chart query' });
      return;
    }

    onSave(queryConfig, chartName.trim() || chart.name);
    onClose();
  }, [chart, questionConfig, answers, chartName, onSave, onClose, setStatus]);

  if (!chart || !questionConfig) return null;

  const isTable = chart.className === 'DotDNADataTable';
  const chartType = getChartTypeByClass(chart.className);
  const previewSize = chartType?.defaultSize ?? '3x2';

  // DataTable: source-grouped column selector + filters only
  if (isTable) {
    const sourceGroups = groupFieldsBySource(assetFields);
    // Narrow by the column search box; groups left with no matches disappear.
    const query = columnSearch.trim().toLowerCase();
    const visibleGroups = query
      ? sourceGroups
          .map((group) => ({
            ...group,
            fields: group.fields.filter((f) => f.name.toLowerCase().includes(query)),
          }))
          .filter((group) => group.fields.length > 0)
      : sourceGroups;
    const selected: string[] = Array.isArray(answers.columns) ? answers.columns : [];
    const toggleCol = (val: string) => {
      const next = selected.includes(val)
        ? selected.filter((v) => v !== val)
        : [...selected, val];
      handleAnswerChange('columns', next.length > 0 ? next : undefined);
    };

    // Extract just the filters section from questionConfig
    const filtersSection = questionConfig.sections.find((s) =>
      s.fields.some((f) => f.type === 'filter-builder'),
    );

    return (
      <Modal isOpen={isOpen} onClose={onClose} title="Configure Data Table" size="xl">
        <InlineStatus status={status} />
        <div className={styles.tableEditor}>
          {/* Name */}
          <div className={styles.nameField}>
            <label className={styles.nameLabel}>Table name</label>
            <input
              type="text"
              className={styles.nameInput}
              value={chartName}
              onChange={(e) => setChartName(e.target.value)}
              placeholder="Data Table"
            />
          </div>

          {/* Filters — placed right after the title so the user can scope
              the dataset before picking which columns to show. */}
          {filtersSection && (
            <QuestionRenderer
              sections={[filtersSection]}
              answers={answers}
              chartMeta={chartMeta}
              assetFields={assetFields}
              onAnswerChange={handleAnswerChange}
            />
          )}

          {/* Columns by source */}
          <div className={styles.columnSectionLabel}>Visible columns</div>
          <input
            type="text"
            className={styles.columnSearchInput}
            placeholder="Search fields..."
            value={columnSearch}
            onChange={(e) => setColumnSearch(e.target.value)}
          />
          {selected.length === 0 && (
            <div className={styles.columnHint}>
              Default: hostname, ip, category, subcategory, os, mac, data_source, last_seen, status
            </div>
          )}
          {visibleGroups.length === 0 && (
            <div className={styles.columnHint}>
              No fields match &ldquo;{columnSearch}&rdquo;
            </div>
          )}
          <div className={styles.sourceColumns}>
            {visibleGroups.map((group) => (
              <div key={group.source} className={styles.sourceColumn}>
                <div className={styles.sourceHeader}>
                  <DataSourceBadge name={group.source} tinted />
                </div>
                {group.fields.map((f) => (
                  <label key={f.value} className={styles.sourceFieldLabel}>
                    <input
                      type="checkbox"
                      checked={selected.includes(f.value)}
                      onChange={() => toggleCol(f.value)}
                    />
                    {f.name}
                  </label>
                ))}
              </div>
            ))}
          </div>

          {/* Rows per page */}
          <div className={styles.rowsPerPage}>
            <label className={styles.nameLabel}>Rows per page</label>
            <select
              className={styles.nameInput}
              value={answers.itemsPerPage ?? 25}
              onChange={(e) => handleAnswerChange('itemsPerPage', Number(e.target.value))}
            >
              <option value={10}>10 rows</option>
              <option value={25}>25 rows</option>
              <option value={50}>50 rows</option>
            </select>
          </div>

          {/* Live preview — same component the card renders, scoped by the
              current columns/filters/itemsPerPage so preview == card. */}
          <div className={styles.tablePreview}>
            <div className={styles.previewHeader}>Preview</div>
            <ReportTable
              columns={debouncedColumns}
              filters={debouncedFilters}
              itemsPerPage={debouncedItemsPerPage}
            />
          </div>
        </div>

        <div className={styles.footer}>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={handleSave}>Save Configuration</Button>
        </div>
      </Modal>
    );
  }

  // Standard chart editor with preview
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Configure ${questionConfig.name}`}
      size="xl"
    >
      <InlineStatus status={status} />
      <div className={styles.panels}>
        {/* Left panel: form */}
        <div className={styles.formPanel}>
          <div className={styles.nameField}>
            <label className={styles.nameLabel}>Chart name</label>
            <input
              type="text"
              className={styles.nameInput}
              value={chartName}
              onChange={(e) => setChartName(e.target.value)}
              placeholder={questionConfig.name}
            />
          </div>
          <QuestionRenderer
            sections={questionConfig.sections}
            answers={answers}
            chartMeta={chartMeta}
            assetFields={assetFields}
            onAnswerChange={handleAnswerChange}
          />
        </div>

        {/* Right panel: preview */}
        <div className={styles.previewPanel}>
          <div className={styles.previewHeader}>Preview</div>
          <div className={styles.previewBody}>
            {/* Mount the chart only once preview data exists — with no data,
                chart classes still paint ghost content (e.g. the donut's
                "0 TOTAL" center text) behind the placeholder below. */}
            {preview.data != null && (
              <DotDNAChart
                key={`${liveQueryConfig?.options?.dashedSegments ?? false}|${liveQueryConfig?.options?.invertColors ?? false}|${liveQueryConfig?.options?.normalize ?? false}`}
                type={chart.className}
                config={{ ...chart.defaultOptions, ...(liveQueryConfig?.options ?? {}) }}
                data={preview.data}
                gridSize={previewSize}
              />
            )}
            {preview.loading && (
              <span className={styles.previewLoading}>Loading preview...</span>
            )}
            {preview.error && (
              <span className={styles.previewError}>{preview.error}</span>
            )}
            {!preview.loading && !preview.error && !preview.data && (
              <span className={styles.previewEmpty}>
                Configure the chart to see a preview
              </span>
            )}
          </div>
        </div>
      </div>

      <div className={styles.footer}>
        <Button variant="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" onClick={handleSave}>
          Save Configuration
        </Button>
      </div>
    </Modal>
  );
}
