// SPDX-License-Identifier: AGPL-3.0-or-later
import { LineChartTile } from '../tiles/LineChartTile';
import { GaugeTile } from '../tiles/GaugeTile';
import type { DashboardProps } from '../MonitoringPage';
import styles from '../MonitoringPage.module.css';

export function RedisDashboard({ start, end, step, rateWindow, refetchInterval }: DashboardProps) {
  return (
    <div className={styles.tilesGrid}>
      <LineChartTile
        title="Hit rate"
        query={`rate(redis_keyspace_hits_total[${rateWindow}]) / (rate(redis_keyspace_hits_total[${rateWindow}]) + rate(redis_keyspace_misses_total[${rateWindow}])) * 100`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="percent"
      />
      <LineChartTile
        title="Memory used"
        query={`redis_memory_used_bytes`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="bytes"
      />
      <LineChartTile
        title="Connected clients"
        query={`redis_connected_clients`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
      />
      <LineChartTile
        title="Evictions per second"
        query={`rate(redis_evicted_keys_total[${rateWindow}])`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="perSec"
      />
      <LineChartTile
        title="Commands per second"
        query={`rate(redis_commands_processed_total[${rateWindow}])`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="perSec"
      />
      <GaugeTile
        title="Memory utilization"
        query={`redis_memory_used_bytes / redis_memory_max_bytes`}
        refetchInterval={refetchInterval}
        max={1}
        thresholds={[70, 90]}
        invert
      />
    </div>
  );
}
