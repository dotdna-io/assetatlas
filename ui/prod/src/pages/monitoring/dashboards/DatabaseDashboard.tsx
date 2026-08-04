// SPDX-License-Identifier: AGPL-3.0-or-later
import { LineChartTile } from '../tiles/LineChartTile';
import { NumberCardTile } from '../tiles/NumberCardTile';
import type { DashboardProps } from '../MonitoringPage';
import styles from '../MonitoringPage.module.css';

export function DatabaseDashboard({ start, end, step, rateWindow, refetchInterval }: DashboardProps) {
  return (
    <div className={styles.tilesGrid}>
      <LineChartTile
        title="Query latency p50/p95/p99"
        query={`label_replace(histogram_quantile(0.50, sum(rate(assetatlas_db_query_duration_seconds_bucket[${rateWindow}])) by (le)), "quantile", "0.5", "", "") or label_replace(histogram_quantile(0.95, sum(rate(assetatlas_db_query_duration_seconds_bucket[${rateWindow}])) by (le)), "quantile", "0.95", "", "") or label_replace(histogram_quantile(0.99, sum(rate(assetatlas_db_query_duration_seconds_bucket[${rateWindow}])) by (le)), "quantile", "0.99", "", "")`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="duration"
        legend={(m) => (m.quantile ? `p${Math.round(parseFloat(m.quantile) * 100)}` : 'p?')}
      />
      <LineChartTile
        title="Active connections by state"
        query={`sum by (state) (pg_stat_activity_count{datname="assetatlas"})`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        legend={(m) => m.state ?? 'unknown'}
      />
      <LineChartTile
        title="Slow queries (>500ms)"
        query={`clamp_min(sum(rate(assetatlas_db_query_duration_seconds_bucket{le="+Inf"}[${rateWindow}])) - sum(rate(assetatlas_db_query_duration_seconds_bucket{le="0.5"}[${rateWindow}])), 0)`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="perSec"
      />
      <LineChartTile
        title="Cache hit ratio"
        query={`pg_stat_database_blks_hit{datname="assetatlas"} / (pg_stat_database_blks_hit{datname="assetatlas"} + pg_stat_database_blks_read{datname="assetatlas"}) * 100`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="percent"
      />
      <NumberCardTile
        title="Database size"
        query={`pg_database_size_bytes{datname="assetatlas"}`}
        refetchInterval={refetchInterval}
        format={(v) => `${(v / 1024 / 1024 / 1024).toFixed(2)} GB`}
      />
    </div>
  );
}
