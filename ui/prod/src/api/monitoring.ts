// SPDX-License-Identifier: AGPL-3.0-or-later
import { keepPreviousData } from '@tanstack/react-query';
import { useApiQuery } from './hooks';

export interface PrometheusQueryResponse {
  resultType: 'vector' | 'matrix' | 'scalar' | 'string';
  result: any[];
}

export interface MonitoringStatus {
  enabled: boolean;
  prometheus_reachable: boolean;
  detail: string | null;
}

/**
 * Monitoring-stack reachability for the Settings status indicator. The
 * /monitoring/status route is NOT gated on the dashboards toggle, so it works
 * whether or not the dashboards are enabled and reports whether the Prometheus
 * containers are actually running. Fetches once on mount (the probe can take up
 * to ~10s when the stack is down) — use refetch() for a manual refresh rather
 * than polling.
 */
export function useMonitoringStatus(enabled = true) {
  return useApiQuery<MonitoringStatus>(
    ['monitoring', 'status'],
    '/monitoring/status',
    { enabled, refetchInterval: false, retry: 0, staleTime: 30_000 },
  );
}

export interface MonitoringStorage {
  reachable: boolean;
  detail: string | null;
  applied_retention: string | null;
  applied_retention_seconds: number | null;
  desired_retention: string;
  pending: boolean;
  disk_used_bytes: number | null;
  head_series: number | null;
  samples_per_second: number | null;
  history_seconds: number | null;
}

/**
 * TSDB storage usage + retention for the Settings panel. Like useMonitoringStatus
 * this is NOT gated on the dashboards toggle and can take a few seconds when the
 * stack is down (the backend pings first and early-returns). Fetch once; use
 * refetch() for a manual refresh rather than polling.
 */
export function useMonitoringStorage(enabled = true) {
  return useApiQuery<MonitoringStorage>(
    ['monitoring', 'storage'],
    '/monitoring/storage',
    { enabled, refetchInterval: false, retry: 0, staleTime: 30_000 },
  );
}

interface QueryOptions {
  enabled?: boolean;
  refetchInterval?: number | false;
}

/**
 * Instant Prometheus query (single point in time).
 * Used by NumberCardTile and GaugeTile.
 */
export function useInstantQuery(
  query: string,
  time?: string,
  opts?: QueryOptions,
) {
  return useApiQuery<PrometheusQueryResponse>(
    ['monitoring', 'query', query, time ?? null],
    '/monitoring/query',
    {
      params: { query, ...(time && { time }) },
      enabled: opts?.enabled ?? true,
      refetchInterval: opts?.refetchInterval ?? false,
      // Keep the last result visible while a refresh (or window shift) loads, so
      // the tile never flips to its loading skeleton — the auto-refresh is invisible.
      placeholderData: keepPreviousData,
    },
  );
}

/**
 * Range Prometheus query (time series).
 * Used by LineChartTile, StackedAreaTile, TableTile.
 */
export function useRangeQuery(
  query: string,
  start: string,
  end: string,
  step: string,
  opts?: QueryOptions,
) {
  return useApiQuery<PrometheusQueryResponse>(
    ['monitoring', 'query_range', query, start, end, step],
    '/monitoring/query_range',
    {
      params: { query, start, end, step },
      enabled: opts?.enabled ?? true,
      refetchInterval: opts?.refetchInterval ?? false,
      // Each auto-refresh slides the window (new start/end → new query key). Keep
      // the previous series on screen while the new one loads so the chart never
      // blanks to a skeleton — the refresh is seamless/invisible.
      placeholderData: keepPreviousData,
    },
  );
}
