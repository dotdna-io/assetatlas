// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect } from 'react';
import { DotDNAChart } from '@/components/charts/DotDNAChart';
import { ReportTable } from './ReportTable';
import { chartsApi } from '@/api/charts';
import { transformToAPIRequest, transformAPIDataForChart, hasRenderableQuery } from '../data/chartDataPipeline';
import { parseSize } from '../data/chartConfig';
import styles from './ChartCard.module.css';

export interface ChartConfig {
  id: number;
  chartId: string;
  className: string;
  name: string;
  size: string;
  allowedSizes: string[];
  gridRow: number;
  gridColumn: number;
  defaultOptions: Record<string, any>;
  queryConfig?: Record<string, any>;
}

interface ChartCardProps {
  chart: ChartConfig;
  index: number;
  isDragging: boolean;
  canResize: boolean;
  onDragStart: (index: number) => void;
  onDragEnd: () => void;
  onDropOnChart: (targetIndex: number) => void;
  onConfigure: (index: number) => void;
  onRemove: (index: number) => void;
  onResize: (index: number) => void;
  readOnly?: boolean;
}

export function ChartCard({
  chart,
  index,
  isDragging,
  canResize,
  onDragStart,
  onDragEnd,
  onDropOnChart,
  onConfigure,
  onRemove,
  onResize,
  readOnly = false,
}: ChartCardProps) {
  const [chartData, setChartData] = useState<any>(undefined);
  const [loading, setLoading] = useState(false);
  const { cols, rows } = parseSize(chart.size);

  // Compute resize chevron direction based on next size in cycle
  const resizeRotation = (() => {
    if (chart.allowedSizes.length <= 1) return 0;
    const idx = chart.allowedSizes.indexOf(chart.size);
    const next = parseSize(chart.allowedSizes[(idx + 1) % chart.allowedSizes.length]);
    const dCol = next.cols - cols;
    const dRow = next.rows - rows;
    if (dCol > 0 && dRow > 0) return 45;    // grow both → bottom-right
    if (dCol > 0 && dRow === 0) return 0;    // grow wider → right
    if (dCol === 0 && dRow > 0) return 90;   // grow taller → down
    if (dCol > 0 && dRow < 0) return -45;    // wider + shorter → upper-right
    if (dCol < 0 && dRow > 0) return 135;    // narrower + taller → lower-left
    if (dCol < 0 && dRow === 0) return 180;  // shrink width → left
    if (dCol === 0 && dRow < 0) return -90;  // shrink height → up
    return -135;                              // shrink both → upper-left
  })();

  // Load chart data from API when queryConfig exists/changes.
  // The `cancelled` flag prevents stale fetches from landing on state when
  // the effect re-runs (StrictMode double-mount in dev, or queryConfig changes).
  // Without it, both fetches resolve and each setChartData triggers another
  // chart animation.
  useEffect(() => {
    if (!hasRenderableQuery(chart.queryConfig)) return;
    // DataTable loads its own data
    if (chart.className === 'DotDNADataTable') return;

    let cancelled = false;
    setLoading(true);
    const apiRequest = transformToAPIRequest(chart.queryConfig);
    chartsApi.query(apiRequest)
      .then((response) => {
        if (cancelled) return;
        // apiClient unwraps { success, data } — response IS the data array
        const transformed = transformAPIDataForChart(response as any, chart);
        setChartData(transformed);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error(`Chart ${chart.name} data load failed:`, err);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [chart.queryConfig, chart.className, chart.name]);

  const gridStyle = {
    gridRow: `${chart.gridRow} / span ${rows}`,
    gridColumn: `${chart.gridColumn} / span ${cols}`,
  };

  const isTable = chart.className === 'DotDNADataTable';
  const cardClasses = [
    styles.card,
    isDragging ? styles.dragging : '',
    isTable ? styles.tableCard : '',
  ].filter(Boolean).join(' ');

  return (
    <div
      className={cardClasses}
      style={gridStyle}
      onDragOver={readOnly ? undefined : (e) => { e.preventDefault(); e.currentTarget.classList.add(styles.dragOver); }}
      onDragLeave={readOnly ? undefined : (e) => e.currentTarget.classList.remove(styles.dragOver)}
      onDrop={readOnly ? undefined : (e) => { e.preventDefault(); e.currentTarget.classList.remove(styles.dragOver); onDropOnChart(index); }}
    >
      {/* Chart name bar */}
      <div className={styles.nameBar}>
        {!readOnly && (
          <div
            className={styles.dragHandle}
            draggable
            onDragStart={() => onDragStart(index)}
            onDragEnd={onDragEnd}
            title="Drag to reorder"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="9" cy="5" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="9" cy="19" r="1" />
              <circle cx="15" cy="5" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="15" cy="19" r="1" />
            </svg>
          </div>
        )}
        <span className={styles.chartName}>{chart.name}</span>
        {!readOnly && (
          <div className={styles.actions}>
            <button className={styles.configBtn} onClick={() => onConfigure(index)} aria-label="Configure" title="Configure">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
            <button className={styles.removeBtn} onClick={() => onRemove(index)} aria-label="Remove" title="Remove">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        )}
      </div>

      {/* Chart body */}
      <div className={styles.chartBody}>
        {chart.className === 'DotDNADataTable' ? (
          <ReportTable
            columns={chart.queryConfig?.options?.columns}
            filters={chart.queryConfig?.filters}
            itemsPerPage={chart.queryConfig?.options?.itemsPerPage}
          />
        ) : (
          <>
            <DotDNAChart
              key={`${chart.queryConfig?.options?.dashedSegments ?? false}|${chart.queryConfig?.options?.invertColors ?? false}|${chart.queryConfig?.options?.normalize ?? false}`}
              type={chart.className}
              config={{ ...chart.defaultOptions, ...(chart.queryConfig?.options ?? {}) }}
              data={chartData}
              gridSize={chart.size}
            />
            {loading && <div className={styles.chartLoading}>Loading...</div>}
          </>
        )}
      </div>

      {/* Resize handle */}
      {!readOnly && chart.allowedSizes.length > 1 && (
        <button
          className={styles.resizeHandle}
          onClick={() => canResize && onResize(index)}
          disabled={!canResize}
          aria-label="Resize chart"
          title={canResize ? 'Resize' : 'Cannot resize here'}
          style={{ cursor: canResize ? 'pointer' : 'default' }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={canResize ? 'var(--accent)' : 'var(--text-muted, #9ca3af)'} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" opacity={canResize ? 1 : 0.4} style={{ transform: `rotate(${resizeRotation}deg)` }}>
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      )}
    </div>
  );
}
