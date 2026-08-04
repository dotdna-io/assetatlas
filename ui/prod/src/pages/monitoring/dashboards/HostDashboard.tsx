// SPDX-License-Identifier: AGPL-3.0-or-later
import { LineChartTile } from '../tiles/LineChartTile';
import type { DashboardProps } from '../MonitoringPage';
import styles from '../MonitoringPage.module.css';

export function HostDashboard({ start, end, step, rateWindow, refetchInterval }: DashboardProps) {
  return (
    <div className={styles.tilesGrid}>
      <LineChartTile
        title="CPU usage %"
        query={`100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[${rateWindow}])) by (instance))`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="percent"
      />
      <LineChartTile
        title="Memory used %"
        query={`(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="percent"
      />
      <LineChartTile
        title="Disk usage % per mountpoint"
        query={`(1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay"}) * 100`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="percent"
        legend={(m) => m.mountpoint ?? 'unknown'}
      />
      <LineChartTile
        title="Network I/O"
        query={`label_replace(sum(rate(node_network_receive_bytes_total[${rateWindow}])), "dir", "rx", "", "") or label_replace(sum(rate(node_network_transmit_bytes_total[${rateWindow}])), "dir", "tx", "", "")`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="bytesPerSec"
        legend={(m) => m.dir ?? 'io'}
      />
      <LineChartTile
        title="Disk I/O"
        query={`label_replace(sum(rate(node_disk_read_bytes_total[${rateWindow}])), "dir", "read", "", "") or label_replace(sum(rate(node_disk_written_bytes_total[${rateWindow}])), "dir", "write", "", "")`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        unit="bytesPerSec"
        legend={(m) => m.dir ?? 'io'}
      />
      <LineChartTile
        title="Load average"
        query={`label_replace(avg(node_load1), "period", "1m", "", "") or label_replace(avg(node_load5), "period", "5m", "", "") or label_replace(avg(node_load15), "period", "15m", "", "")`}
        start={start} end={end} step={step}
        refetchInterval={refetchInterval}
        legend={(m) => m.period ?? 'load'}
      />
    </div>
  );
}
