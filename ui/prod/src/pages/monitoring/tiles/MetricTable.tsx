// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './MetricTable.module.css';

export interface MetricTableColumn {
  label: string;
  key: string;
}

interface MetricTableProps {
  columns: MetricTableColumn[];
  rows: Array<Record<string, string | number>>;
}

/**
 * Compact, presentational top-N table for monitoring tiles. Deliberately not the
 * interactive DotDNADataTable (search / pagination / column-picker) — a monitoring
 * panel is a static leaderboard, and that chrome does not fit a 220px tile. The
 * trailing "Value" column is right-aligned; label columns truncate with a tooltip.
 */
export function MetricTable({ columns, rows }: MetricTableProps) {
  const cols: MetricTableColumn[] = [...columns, { label: 'Value', key: 'value' }];

  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} className={c.key === 'value' ? styles.num : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {cols.map((c) => {
                const cell = row[c.key] ?? '';
                return (
                  <td
                    key={c.key}
                    className={c.key === 'value' ? styles.num : undefined}
                    title={String(cell)}
                  >
                    {cell}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
