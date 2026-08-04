// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { DotDNAChart } from '@/components/charts/DotDNAChart';
import { useRangeQuery } from '@/api/monitoring';
import { Tile } from './Tile';
import { mapMatrixToStackedColumn } from './tileUtils';

interface StackedAreaTileProps {
  title: string;
  query: string;
  start: string;
  end: string;
  step: string;
  refetchInterval: number | false;
  groupBy: (labels: Record<string, string>) => string;
}

export function StackedAreaTile({
  title, query, start, end, step, refetchInterval, groupBy,
}: StackedAreaTileProps) {
  const q = useRangeQuery(query, start, end, step, { refetchInterval });

  const chartData = useMemo(
    () => mapMatrixToStackedColumn(q.data, groupBy),
    [q.data, groupBy],
  );

  const isEmpty = !!chartData && chartData.length === 0;

  return (
    <Tile
      title={title}
      isLoading={q.isLoading}
      error={q.error}
      isEmpty={isEmpty}
      onRetry={() => q.refetch()}
    >
      <DotDNAChart
        type="DotDNAStackedColumn"
        config={{ compactLegend: true }}
        data={chartData}
      />
    </Tile>
  );
}
