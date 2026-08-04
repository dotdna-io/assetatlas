// SPDX-License-Identifier: AGPL-3.0-or-later
import { LineChartTile } from '../tiles/LineChartTile';
import { TableTile } from '../tiles/TableTile';
import type { DashboardProps } from '../MonitoringPage';
import styles from '../MonitoringPage.module.css';

export function SystemDashboard({ start, end, step, rateWindow, refetchInterval }: DashboardProps) {
  return (
    <div className={styles.tilesGrid}>
      <LineChartTile
        title="Request rate"
        query={`sum(rate(assetatlas_http_request_total[${rateWindow}]))`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="perSec"
      />
      <LineChartTile
        title="Error rate (4xx vs 5xx)"
        query={`label_replace(sum(rate(assetatlas_http_request_total{status=~"4.."}[${rateWindow}])), "class", "4xx", "", "") or label_replace(sum(rate(assetatlas_http_request_total{status=~"5.."}[${rateWindow}])), "class", "5xx", "", "")`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="perSec"
        legend={(m) => m.class ?? 'other'}
      />
      <LineChartTile
        title="Latency p50/p95/p99"
        query={`label_replace(histogram_quantile(0.50, sum(rate(assetatlas_http_request_duration_seconds_bucket[${rateWindow}])) by (le)), "quantile", "0.5", "", "") or label_replace(histogram_quantile(0.95, sum(rate(assetatlas_http_request_duration_seconds_bucket[${rateWindow}])) by (le)), "quantile", "0.95", "", "") or label_replace(histogram_quantile(0.99, sum(rate(assetatlas_http_request_duration_seconds_bucket[${rateWindow}])) by (le)), "quantile", "0.99", "", "")`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="duration"
        legend={(m) => (m.quantile ? `p${Math.round(parseFloat(m.quantile) * 100)}` : 'p?')}
      />
      <TableTile
        title="Top endpoints by p95 latency"
        query={`topk(10, histogram_quantile(0.95, sum(rate(assetatlas_http_request_duration_seconds_bucket[5m])) by (le, endpoint)))`}
        refetchInterval={refetchInterval}
        columns={[{ label: 'Endpoint', key: 'endpoint' }]}
        formatValue={(v) => `${(v * 1000).toFixed(0)} ms`}
      />
      <TableTile
        title="Top endpoints by 5xx rate"
        query={`topk(10, sum by (endpoint) (rate(assetatlas_http_request_duration_seconds_count{status=~"5.."}[5m])))`}
        refetchInterval={refetchInterval}
        columns={[{ label: 'Endpoint', key: 'endpoint' }]}
        formatValue={(v) => `${v.toFixed(2)}/s`}
      />
    </div>
  );
}
