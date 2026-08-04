// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useMemo } from 'react';
import { ChartCard } from './ChartCard';
import { useDragAndDrop } from './useDragAndDrop';
import { cycleSize } from './useGridCollisions';
import type { ChartConfig } from './ChartCard';
import styles from './ChartGrid.module.css';

interface ChartGridProps {
  charts: ChartConfig[];
  onChartsChange: (charts: ChartConfig[]) => void;
  onConfigureChart: (index: number) => void;
  readOnly?: boolean;
}

export function ChartGrid({ charts, onChartsChange, onConfigureChart, readOnly = false }: ChartGridProps) {
  const {
    dragState,
    handleDragStart,
    handleDragEnd,
    handleDropOnChart,
    handleDropOnCell,
    getPlaceholderCells,
  } = useDragAndDrop(charts, onChartsChange);

  const handleRemove = useCallback(
    (index: number) => {
      onChartsChange(charts.filter((_, i) => i !== index));
    },
    [charts, onChartsChange],
  );

  const handleResize = useCallback(
    (index: number) => {
      const chart = charts[index];
      const newSize = cycleSize(index, charts, chart.allowedSizes);
      if (newSize) {
        const updated = [...charts];
        updated[index] = { ...updated[index], size: newSize };
        onChartsChange(updated);
      }
    },
    [charts, onChartsChange],
  );

  const placeholders = getPlaceholderCells();

  // Pre-compute which charts can actually resize (next size differs from current)
  const canResizeMap = useMemo(() =>
    charts.map((chart, idx) => {
      if (chart.allowedSizes.length <= 1) return false;
      const next = cycleSize(idx, charts, chart.allowedSizes);
      return next !== null && next !== chart.size;
    }),
    [charts],
  );

  return (
    <div className={styles.grid}>
      {charts.map((chart, idx) => (
        <ChartCard
          key={chart.id}
          chart={chart}
          index={idx}
          isDragging={dragState.dragIndex === idx}
          canResize={canResizeMap[idx]}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDropOnChart={handleDropOnChart}
          onConfigure={onConfigureChart}
          onRemove={handleRemove}
          onResize={handleResize}
          readOnly={readOnly}
        />
      ))}

      {!readOnly && placeholders.map(({ row, col }) => (
        <div
          key={`ph-${row}-${col}`}
          className={styles.placeholder}
          style={{ gridRow: row, gridColumn: col }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={() => handleDropOnCell(row, col)}
        />
      ))}
    </div>
  );
}
