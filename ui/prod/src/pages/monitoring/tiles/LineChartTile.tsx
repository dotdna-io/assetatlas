// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { DotDNAChart } from '@/components/charts/DotDNAChart';
import { useRangeQuery } from '@/api/monitoring';
import { Tile } from './Tile';
import { mapMatrixToLineChart, defaultSeriesLabel } from './tileUtils';

/** Value unit for axis/tooltip formatting (maps to DotDNALineChart's `unit`). */
export type ChartUnit = 'percent' | 'bytes' | 'bytesPerSec' | 'duration' | 'short' | 'perSec';

interface LineChartTileProps {
  title: string;
  query: string;
  start: string;
  end: string;
  step: string;
  refetchInterval: number | false;
  legend?: (labels: Record<string, string>) => string;
  unit?: ChartUnit;
}

export function LineChartTile({
  title, query, start, end, step, refetchInterval, legend, unit,
}: LineChartTileProps) {
  const q = useRangeQuery(query, start, end, step, { refetchInterval });

  const chartData = useMemo(
    () => mapMatrixToLineChart(q.data, legend ?? defaultSeriesLabel),
    [q.data, legend],
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
        type="DotDNALineChart"
        config={{ unit, compactLegend: true, animate: false, showDots: false }}
        data={chartData}
      />
    </Tile>
  );
}
