// SPDX-License-Identifier: AGPL-3.0-or-later
import { DotDNAChart } from '@/components/charts/DotDNAChart';
import { useInstantQuery } from '@/api/monitoring';
import { Tile } from './Tile';
import { extractScalar } from './tileUtils';

interface NumberCardTileProps {
  title: string;
  query: string;
  refetchInterval: number | false;
  format?: (v: number) => string;
  suffix?: string;
}

export function NumberCardTile({
  title, query, refetchInterval, format, suffix,
}: NumberCardTileProps) {
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
      <DotDNAChart
        type="DotDNANumberCard"
        config={{ suffix, animate: false }}
        data={value !== null ? { value: format ? format(value) : value } : null}
      />
    </Tile>
  );
}
