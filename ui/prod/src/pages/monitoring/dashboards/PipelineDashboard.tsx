// SPDX-License-Identifier: AGPL-3.0-or-later
import { LineChartTile } from '../tiles/LineChartTile';
import type { DashboardProps } from '../MonitoringPage';
import styles from '../MonitoringPage.module.css';

export function PipelineDashboard({ start, end, step, rateWindow, refetchInterval }: DashboardProps) {
  return (
    <div className={styles.tilesGrid}>
      <LineChartTile
        title="Run duration p95"
        query={`histogram_quantile(0.95, sum(rate(assetatlas_asset_generation_duration_seconds_bucket[${rateWindow}])) by (le))`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="duration"
      />
      <LineChartTile
        title="Stage timing breakdown"
        query={`sum by (stage) (rate(assetatlas_pipeline_stage_duration_seconds_sum[${rateWindow}]))`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="duration"
        legend={(m) => m.stage ?? 'unknown'}
      />
    </div>
  );
}
