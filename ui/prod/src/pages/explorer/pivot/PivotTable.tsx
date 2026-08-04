// SPDX-License-Identifier: AGPL-3.0-or-later
import { useAssetAggregate } from '@/api/assets';
import type { AggregateResponse } from '@/api/assets';
import { decodeFieldId } from '@/types/field-id';
import styles from './pivot.module.css';

// Strip the FieldId kind prefix ("static:", "virtual:", "posture_state:" …)
// and decode percent-encoded segments so the pivot header reads
// "Category" instead of "static:Category" or "IP Address" instead of
// "static:IP%20Address". Falls back to the raw string on any error.
function fieldDisplayLabel(fieldId: string): string {
  try {
    const f = decodeFieldId(fieldId);
    if ('name' in f && typeof f.name === 'string') return f.name;
    if ('category' in f && typeof (f as { category: string }).category === 'string') {
      return (f as { category: string }).category;
    }
    return fieldId;
  } catch {
    return fieldId;
  }
}

interface PivotTableProps {
  rowField: string;
  colField?: string;
  filters?: string;
  displayMode: 'count' | 'percent_total' | 'percent_row' | 'percent_col';
}

function formatValue(
  count: number,
  rowTotal: number,
  colTotal: number,
  grandTotal: number,
  displayMode: PivotTableProps['displayMode'],
): string {
  if (displayMode === 'count') {
    return count.toLocaleString();
  }

  let percentage = 0;
  if (displayMode === 'percent_total') {
    percentage = grandTotal > 0 ? (count / grandTotal) * 100 : 0;
  } else if (displayMode === 'percent_row') {
    percentage = rowTotal > 0 ? (count / rowTotal) * 100 : 0;
  } else if (displayMode === 'percent_col') {
    percentage = colTotal > 0 ? (count / colTotal) * 100 : 0;
  }

  return percentage === 0 ? '0%' : percentage.toFixed(1) + '%';
}

function CrossTabTable({
  data,
  rowField,
  displayMode,
}: {
  data: AggregateResponse;
  rowField: string;
  displayMode: PivotTableProps['displayMode'];
}) {
  const { rows, columns, data: dataMap, grand_total } = data;

  // Lookup helper: data is { rowKey: { colKey: count } }
  const cellValue = (row: string, col: string): number => dataMap[row]?.[col] ?? 0;

  // Build row totals and col totals
  const rowTotals: number[] = rows.map((row) =>
    columns.reduce((sum, col) => sum + cellValue(row, col), 0),
  );
  const colTotals: number[] = columns.map((col) =>
    rows.reduce((sum, row) => sum + cellValue(row, col), 0),
  );

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.rowHeader}>{fieldDisplayLabel(rowField)}</th>
            {columns.map((col) => (
              <th key={col} className={styles.colHeader}>
                {col}
              </th>
            ))}
            <th className={styles.totalHeader}>Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={row} className={ri % 2 === 1 ? styles.rowAlt : undefined}>
              <td className={styles.rowLabel}>{row}</td>
              {columns.map((col, ci) => {
                const count = cellValue(row, col);
                return (
                  <td key={col} className={styles.cell}>
                    {formatValue(count, rowTotals[ri], colTotals[ci], grand_total, displayMode)}
                  </td>
                );
              })}
              <td className={styles.rowTotal}>
                {displayMode === 'percent_row'
                  ? '100%'
                  : displayMode === 'count'
                  ? rowTotals[ri].toLocaleString()
                  : formatValue(rowTotals[ri], rowTotals[ri], grand_total, grand_total, 'percent_total')}
              </td>
            </tr>
          ))}
          <tr className={styles.totalRow}>
            <td className={styles.rowLabel}>Total</td>
            {columns.map((col, ci) => (
              <td key={col} className={styles.cell}>
                {displayMode === 'percent_col'
                  ? '100%'
                  : displayMode === 'count'
                  ? (colTotals[ci] ?? 0).toLocaleString()
                  : formatValue(colTotals[ci] ?? 0, grand_total, colTotals[ci], grand_total, 'percent_total')}
              </td>
            ))}
            <td className={styles.grandTotal}>
              {displayMode === 'count' ? grand_total.toLocaleString() : '100%'}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function RowOnlyTable({
  data,
  rowField,
  displayMode,
}: {
  data: AggregateResponse;
  rowField: string;
  displayMode: PivotTableProps['displayMode'];
}) {
  const { rows, data: dataMap, grand_total } = data;

  // row-only: data is { rowKey: count } or { rowKey: { "": count } }
  const sorted = rows
    .map((row) => {
      const val = dataMap[row];
      const count = typeof val === 'number' ? val : (val ? Object.values(val)[0] ?? 0 : 0);
      return { row, count };
    })
    .sort((a, b) => b.count - a.count);

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.rowHeader}>{fieldDisplayLabel(rowField)}</th>
            <th className={styles.totalHeader}>Count</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(({ row, count }, i) => (
            <tr key={row} className={i % 2 === 1 ? styles.rowAlt : undefined}>
              <td className={styles.rowLabel}>{row}</td>
              <td className={styles.cell}>
                {formatValue(count, grand_total, grand_total, grand_total, displayMode)}
              </td>
            </tr>
          ))}
          <tr className={styles.totalRow}>
            <td className={styles.rowLabel}>Total</td>
            <td className={styles.grandTotal}>
              {displayMode === 'count' ? grand_total.toLocaleString() : '100%'}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function PivotTable({ rowField, colField, filters, displayMode }: PivotTableProps) {
  const { data, isLoading, isError } = useAssetAggregate({
    row_field: rowField,
    col_field: colField || undefined,
    filters,
  });

  if (isLoading) {
    return <div className={styles.message}>Loading...</div>;
  }

  if (isError || !data) {
    return <div className={styles.message}>Failed to load pivot data.</div>;
  }

  if (data.grand_total === 0) {
    return <div className={styles.message}>No data matches the current filters.</div>;
  }

  if (colField) {
    return <CrossTabTable data={data} rowField={rowField} displayMode={displayMode} />;
  }

  return <RowOnlyTable data={data} rowField={rowField} displayMode={displayMode} />;
}
