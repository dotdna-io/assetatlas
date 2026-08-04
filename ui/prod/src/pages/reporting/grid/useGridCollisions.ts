// SPDX-License-Identifier: AGPL-3.0-or-later
import { parseSize } from '../data/chartConfig';

export interface ChartPosition {
  gridRow: number;
  gridColumn: number;
  size: string; // 'WxH' format
}

const GRID_COLS = 6;
const MAX_ROWS = 20;

/** Get Set of occupied "row,col" strings for all charts except excludeIndex */
export function getOccupiedCells(
  charts: ChartPosition[],
  excludeIndex?: number,
): Set<string> {
  const occupied = new Set<string>();
  charts.forEach((chart, i) => {
    if (i === excludeIndex) return;
    const { cols, rows } = parseSize(chart.size);
    for (let r = chart.gridRow; r < chart.gridRow + rows; r++) {
      for (let c = chart.gridColumn; c < chart.gridColumn + cols; c++) {
        occupied.add(`${r},${c}`);
      }
    }
  });
  return occupied;
}

/** Check if a rectangle at (row, col) with given size collides with occupied cells */
export function checkCollision(
  row: number,
  col: number,
  width: number,
  height: number,
  occupied: Set<string>,
): boolean {
  if (col + width > GRID_COLS + 1) return true;
  for (let r = row; r < row + height; r++) {
    for (let c = col; c < col + width; c++) {
      if (occupied.has(`${r},${c}`)) return true;
    }
  }
  return false;
}

/** Find first empty position for a chart of given size */
export function findEmptyPosition(
  width: number,
  height: number,
  charts: ChartPosition[],
): { gridRow: number; gridColumn: number } {
  const occupied = getOccupiedCells(charts);
  for (let row = 1; row <= MAX_ROWS; row++) {
    for (let col = 1; col <= GRID_COLS - width + 1; col++) {
      if (!checkCollision(row, col, width, height, occupied)) {
        return { gridRow: row, gridColumn: col };
      }
    }
  }
  // Fallback: place below everything
  const maxRow = charts.reduce((max, c) => {
    const { rows } = parseSize(c.size);
    return Math.max(max, c.gridRow + rows);
  }, 1);
  return { gridRow: maxRow, gridColumn: 1 };
}

/** Cycle to the next allowed size that doesn't collide */
export function cycleSize(
  chartIndex: number,
  charts: ChartPosition[],
  allowedSizes: string[],
): string | null {
  const chart = charts[chartIndex];
  const currentIdx = allowedSizes.indexOf(chart.size);
  const occupied = getOccupiedCells(charts, chartIndex);

  for (let i = 1; i <= allowedSizes.length; i++) {
    const nextIdx = (currentIdx + i) % allowedSizes.length;
    const nextSize = allowedSizes[nextIdx];
    const { cols, rows } = parseSize(nextSize);
    if (!checkCollision(chart.gridRow, chart.gridColumn, cols, rows, occupied)) {
      return nextSize;
    }
  }
  return null; // No valid size found
}
