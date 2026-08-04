// SPDX-License-Identifier: AGPL-3.0-or-later
import { LineChartTile } from '../tiles/LineChartTile';
import { TableTile } from '../tiles/TableTile';
import { useInstantQuery } from '@/api/monitoring';
import { extractScalar } from '../tiles/tileUtils';
import type { DashboardProps } from '../MonitoringPage';
import styles from '../MonitoringPage.module.css';

export function ContainersDashboard({ start, end, step, rateWindow, refetchInterval }: DashboardProps) {
  // cAdvisor produces every container_* series; the friendly `name` label comes
  // from its Docker factory. If cadvisor isn't scraped — or can't read the
  // container runtime (rootless Podman, or Docker Desktop's VM) — no name-labelled
  // series exist and all six panels are empty, so tell the operator why rather
  // than leaving blank tiles.
  const probe = useInstantQuery(`count(container_last_seen{name!=""}) OR on() vector(0)`, undefined, { refetchInterval });
  const seen = extractScalar(probe.data);
  const cadvisorMissing = probe.data !== undefined && (seen === null || seen === 0);

  return (
    <>
      {cadvisorMissing && (
        <div className={styles.notice}>
          Container metrics come from the <span className={styles.code}>cadvisor</span> exporter,
          which isn’t reporting named containers here. It only runs on Docker installs —
          under Podman it is disabled by design, so this panel stays empty on those hosts.
          On a Docker install, check that the cadvisor container is running and that
          Prometheus is scraping it.
        </div>
      )}
      <div className={styles.tilesGrid}>
        <LineChartTile
          title="CPU per container"
          query={`sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[${rateWindow}]))`}
          start={start} end={end} step={step}
          refetchInterval={refetchInterval}
          unit="short"
          legend={(m) => m.name ?? 'unknown'}
        />
        <LineChartTile
          title="Memory per container"
          query={`sum by (name) (container_memory_usage_bytes{name!=""})`}
          start={start} end={end} step={step}
          refetchInterval={refetchInterval}
          unit="bytes"
          legend={(m) => m.name ?? 'unknown'}
        />
        <LineChartTile
          title="Network I/O per container"
          query={`sum by (name) (rate(container_network_receive_bytes_total{name!=""}[${rateWindow}]))`}
          start={start} end={end} step={step}
          refetchInterval={refetchInterval}
          unit="bytesPerSec"
          legend={(m) => m.name ?? 'unknown'}
        />
        <TableTile
          title="Container uptime"
          query={`time() - container_start_time_seconds{name!=""}`}
          refetchInterval={refetchInterval}
          columns={[{ label: 'Container', key: 'name' }]}
          formatValue={(v) => (v < 3600 ? `${(v / 60).toFixed(0)}m` : v < 86400 ? `${(v / 3600).toFixed(1)}h` : `${(v / 86400).toFixed(1)}d`)}
        />
      </div>
    </>
  );
}
