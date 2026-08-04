// SPDX-License-Identifier: AGPL-3.0-or-later
import { DotDNAChart } from '@/components/charts/DotDNAChart';
import { useInstantQuery } from '@/api/monitoring';
import { Tile } from './Tile';
import { extractScalar } from './tileUtils';

interface GaugeTileProps {
  title: string;
  query: string;
  refetchInterval: number | false;
  /** Full-scale value; the gauge shows value/max as a percentage. */
  max?: number;
  /** [low, high] band edges as a percentage of max (e.g. [33, 66]). */
  thresholds?: [number, number];
  /** true → a high reading is "bad" (red at the top of the scale). */
  invert?: boolean;
}

export function GaugeTile({
  title, query, refetchInterval, max = 100, thresholds = [33, 66], invert = false,
}: GaugeTileProps) {
  const q = useInstantQuery(query, undefined, { refetchInterval });

  const value = extractScalar(q.data);
  const isEmpty = q.data !== undefined && value === null;

  return (
    <Tile
      title={title}
      isLoading={q.isLoading}
      error={q.error}
      isEmpty={isEmpty}
      onRetry={() => q.refetch()}
    >
      {/* DotDNALinearGauge reads value from array-shaped data[0].value and
          maxValue/thresholdLow/thresholdHigh/invertColors from config — passing
          the {min,max,colorStops}/{value} shape leaves it stuck on its default. */}
      <DotDNAChart
        type="DotDNALinearGauge"
        config={{
          maxValue: max,
          thresholdLow: thresholds[0],
          thresholdHigh: thresholds[1],
          invertColors: invert,
        }}
        data={value !== null ? [{ value }] : null}
      />
    </Tile>
  );
}
