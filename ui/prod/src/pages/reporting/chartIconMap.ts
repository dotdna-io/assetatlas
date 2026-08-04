// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Maps chart type identifiers to their SVG icon filenames in /chart-icons/.
 * Used by ReportCard to render the miniature report preview grid.
 */

const CHART_ICON_MAP: Record<string, string> = {
  'line-chart': 'line-chart.svg',
  'pie-chart': 'pie-chart.svg',
  'vertical-bar-chart': 'vertical-bar-chart.svg',
  'horizontal-bar-chart': 'horizontal-bar-chart.svg',
  'donut-chart': 'donut-chart.svg',
  'data-table': 'data-table.svg',
  'gauge': 'gauge.svg',
  'number-chart': 'number-chart.svg',
  'metric-card': 'metric-card.svg',
  'metric-spark': 'metric-spark.svg',
  'metric-bar': 'metric-bar.svg',
  'circle-chart': 'circle-chart.svg',
  'circle-gauge': 'circle-gauge.svg',
  'linear-gauge': 'linear-gauge.svg',
  'force-graph-chart': 'force-graph-chart.svg',
  'sankey-diagram': 'sankey-diagram.svg',
  'segmented-radial-chart': 'segmented-radial-chart.svg',
  'radial-bar-chart': 'radial-bar-chart.svg',
  'butterfly-chart': 'butterfly-chart.svg',
  'timeseries-butterfly': 'timeseries-butterfly.svg',
  'punchcard': 'punchcard.svg',
  'stacked-column-chart': 'stacked-column-chart.svg',
};

const DEFAULT_ICON = 'vertical-bar-chart.svg';

/**
 * Get the icon path for a chart configuration.
 * Checks `type` field first, then `chartId`, falls back to default.
 */
export function getChartIconPath(chart: Record<string, any>): string {
  const type = chart.type || chart.chartType || chart.chartId || '';
  const filename = CHART_ICON_MAP[type] || DEFAULT_ICON;
  return `/chart-icons/${filename}`;
}

/**
 * Group charts into rows based on their `size` property.
 * - 'full' = takes entire row
 * - 'third' = 3 per row
 * - 'half' (default) = 2 per row
 *
 * Returns at most `maxRows` rows for the preview.
 */
export function groupChartsIntoRows(
  charts: Record<string, any>[],
  maxRows = 3,
): Record<string, any>[][] {
  const rows: Record<string, any>[][] = [];
  let currentRow: Record<string, any>[] = [];
  let currentRowWidth = 0;

  for (const chart of charts) {
    if (rows.length >= maxRows) break;

    const size = chart.size || 'half';
    const width = size === 'full' ? 1 : size === 'third' ? 1 / 3 : 0.5;

    if (currentRowWidth + width > 1 + 0.01) {
      rows.push(currentRow);
      currentRow = [];
      currentRowWidth = 0;
      if (rows.length >= maxRows) break;
    }

    currentRow.push(chart);
    currentRowWidth += width;

    if (currentRowWidth >= 1 - 0.01) {
      rows.push(currentRow);
      currentRow = [];
      currentRowWidth = 0;
    }
  }

  if (currentRow.length > 0 && rows.length < maxRows) {
    rows.push(currentRow);
  }

  return rows;
}
