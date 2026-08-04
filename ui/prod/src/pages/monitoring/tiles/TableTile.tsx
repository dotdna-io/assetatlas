// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { useInstantQuery } from '@/api/monitoring';
import { Tile } from './Tile';
import { MetricTable } from './MetricTable';

interface TableTileProps {
  title: string;
  query: string;
  refetchInterval: number | false;
  columns: Array<{ label: string; key: string }>;
  formatValue?: (v: number) => string;
}

export function TableTile({
  title, query, refetchInterval, columns, formatValue,
}: TableTileProps) {
  const q = useInstantQuery(query, undefined, { refetchInterval });

  const rows = useMemo(() => {
    if (!q.data || q.data.resultType !== 'vector') return null;
    return q.data.result.map((r: any) => {
      const row: Record<string, string | number> = {};
      for (const col of columns) {
        row[col.key] = r.metric?.[col.key] ?? '';
      }
      const v = parseFloat(r.value?.[1] ?? '');
      row.value = !Number.isNaN(v) && formatValue ? formatValue(v) : (Number.isNaN(v) ? '' : v);
      return row;
    });
  }, [q.data, columns, formatValue]);

  const isEmpty = !!rows && rows.length === 0;

  return (
    <Tile
      title={title}
      isLoading={q.isLoading}
      error={q.error}
      isEmpty={isEmpty}
      onRetry={() => q.refetch()}
    >
      <MetricTable columns={columns} rows={rows ?? []} />
    </Tile>
  );
}
