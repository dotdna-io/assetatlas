// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback, useRef } from 'react';
import { parseSize } from '../data/chartConfig';
import { getOccupiedCells, checkCollision } from './useGridCollisions';

interface ChartWithPosition {
  gridRow: number;
  gridColumn: number;
  size: string;
  [key: string]: any;
}

const GRID_COLS = 6;

interface DragState {
  isDragging: boolean;
  dragIndex: number | null;
}

export function useDragAndDrop(
  charts: ChartWithPosition[],
  onChartsChange: (charts: ChartWithPosition[]) => void,
) {
  const [dragState, setDragState] = useState<DragState>({
    isDragging: false,
    dragIndex: null,
  });
  const dragIndexRef = useRef<number | null>(null);

  const handleDragStart = useCallback((index: number) => {
    dragIndexRef.current = index;
    setDragState({ isDragging: true, dragIndex: index });
  }, []);

  const handleDragEnd = useCallback(() => {
    dragIndexRef.current = null;
    setDragState({ isDragging: false, dragIndex: null });
  }, []);

  /** Drop on another chart card -- swap positions */
  const handleDropOnChart = useCallback(
    (targetIndex: number) => {
      const srcIdx = dragIndexRef.current;
      if (srcIdx === null || srcIdx === targetIndex) return;

      const updated = [...charts];
      const srcPos = { gridRow: updated[srcIdx].gridRow, gridColumn: updated[srcIdx].gridColumn };
      const tgtPos = { gridRow: updated[targetIndex].gridRow, gridColumn: updated[targetIndex].gridColumn };

      updated[srcIdx] = { ...updated[srcIdx], gridRow: tgtPos.gridRow, gridColumn: tgtPos.gridColumn };
      updated[targetIndex] = { ...updated[targetIndex], gridRow: srcPos.gridRow, gridColumn: srcPos.gridColumn };

      onChartsChange(updated);
    },
    [charts, onChartsChange],
  );

  /** Drop on an empty cell -- move chart there if no collision */
  const handleDropOnCell = useCallback(
    (targetRow: number, targetCol: number) => {
      const srcIdx = dragIndexRef.current;
      if (srcIdx === null) return;

      const chart = charts[srcIdx];
      const { cols, rows } = parseSize(chart.size);
      const occupied = getOccupiedCells(charts, srcIdx);

      if (!checkCollision(targetRow, targetCol, cols, rows, occupied)) {
        const updated = [...charts];
        updated[srcIdx] = { ...updated[srcIdx], gridRow: targetRow, gridColumn: targetCol };
        onChartsChange(updated);
      }
    },
    [charts, onChartsChange],
  );

  /** Generate placeholder cells for empty grid positions during drag */
  const getPlaceholderCells = useCallback((): { row: number; col: number }[] => {
    if (!dragState.isDragging || dragState.dragIndex === null) return [];
    const occupied = getOccupiedCells(charts, dragState.dragIndex);
    const maxRow = charts.reduce((max, c) => {
      const { rows } = parseSize(c.size);
      return Math.max(max, c.gridRow + rows);
    }, 1);
    const cells: { row: number; col: number }[] = [];
    for (let r = 1; r <= maxRow + 1; r++) {
      for (let c = 1; c <= GRID_COLS; c++) {
        if (!occupied.has(`${r},${c}`)) {
          cells.push({ row: r, col: c });
        }
      }
    }
    return cells;
  }, [charts, dragState.isDragging]);

  return {
    dragState,
    handleDragStart,
    handleDragEnd,
    handleDropOnChart,
    handleDropOnCell,
    getPlaceholderCells,
  };
}
