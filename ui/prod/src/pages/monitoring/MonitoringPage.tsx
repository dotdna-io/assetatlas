// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useDocumentTitle } from '@/hooks';
import { useHealth } from '@/api/settings';

import { DashboardTabs } from './DashboardTabs';
import { TimeRangePicker } from './TimeRangePicker';
import { RefreshControl } from './RefreshControl';
import { MonitoringEnableBar } from './MonitoringEnableBar';
import { MonitoringRetentionPanel } from './MonitoringRetentionPanel';
import { useMonitoringAvailable } from './useMonitoringAvailable';
import { rangeToParams, refreshToMs } from './timeRange';
import type { CustomRange, RangePreset, RefreshInterval, TabId } from './types';

import { SystemDashboard } from './dashboards/SystemDashboard';
import { DatabaseDashboard } from './dashboards/DatabaseDashboard';
import { RedisDashboard } from './dashboards/RedisDashboard';
import { PipelineDashboard } from './dashboards/PipelineDashboard';
import { HostDashboard } from './dashboards/HostDashboard';
import { ContainersDashboard } from './dashboards/ContainersDashboard';

import styles from './MonitoringPage.module.css';

const DEFAULT_TAB: TabId = 'system';
const DEFAULT_PRESET: RangePreset = '1h';
const DEFAULT_REFRESH: RefreshInterval = '30s';

const VALID_TABS: TabId[] = ['system', 'database', 'redis', 'pipeline', 'host', 'containers'];
const VALID_PRESETS: RangePreset[] = ['1h', '6h', '24h', '7d', 'custom'];
const VALID_REFRESH: RefreshInterval[] = ['off', '10s', '30s', '1m', '5m'];

function parseTab(v: string | null): TabId {
  return (VALID_TABS as string[]).includes(v ?? '') ? (v as TabId) : DEFAULT_TAB;
}
function parsePreset(v: string | null): RangePreset {
  return (VALID_PRESETS as string[]).includes(v ?? '') ? (v as RangePreset) : DEFAULT_PRESET;
}
function parseRefresh(v: string | null): RefreshInterval {
  return (VALID_REFRESH as string[]).includes(v ?? '') ? (v as RefreshInterval) : DEFAULT_REFRESH;
}

export interface DashboardProps {
  start: string;
  end: string;
  step: string;
  rateWindow: string;
  refetchInterval: number | false;
}

export default function MonitoringPage() {
  useDocumentTitle('Monitoring');
  const monitoringAvailable = useMonitoringAvailable();
  // `health` distinguishes "not loaded yet" (undefined) from "loaded, monitoring
  // off": both make monitoringAvailable false, but only the latter should show
  // the notice — see the guard below.
  const { data: health } = useHealth();
  const [params, setParams] = useSearchParams();

  const tab = parseTab(params.get('tab'));
  const preset = parsePreset(params.get('range'));
  const refresh = parseRefresh(params.get('refresh'));

  const custom: CustomRange | undefined =
    preset === 'custom'
      ? {
          start: parseInt(params.get('start') ?? '0', 10),
          end: parseInt(params.get('end') ?? '0', 10),
        }
      : undefined;

  const setTab = (next: TabId) => {
    const p = new URLSearchParams(params);
    if (next === DEFAULT_TAB) p.delete('tab'); else p.set('tab', next);
    setParams(p, { replace: true });
  };
  const setRange = (nextPreset: RangePreset, nextCustom?: CustomRange) => {
    const p = new URLSearchParams(params);
    if (nextPreset === DEFAULT_PRESET) p.delete('range'); else p.set('range', nextPreset);
    if (nextPreset === 'custom' && nextCustom) {
      p.set('start', String(nextCustom.start));
      p.set('end', String(nextCustom.end));
    } else {
      p.delete('start');
      p.delete('end');
    }
    setParams(p, { replace: true });
  };
  const setRefresh = (next: RefreshInterval) => {
    const p = new URLSearchParams(params);
    if (next === DEFAULT_REFRESH) p.delete('refresh'); else p.set('refresh', next);
    setParams(p, { replace: true });
  };

  // `tick` advances on every auto-refresh interval and on manual refresh.
  // Including it in `dashboardProps` deps causes `rangeToParams` to recapture
  // `now`, which slides the window forward for relative presets ('1h', '6h',
  // '24h', '7d') so tiles fetch fresh data instead of repeatedly re-querying
  // the window captured at first render.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ms = refreshToMs(refresh);
    if (ms === false) return;
    const id = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(id);
  }, [refresh]);

  const dashboardProps: DashboardProps = useMemo(() => {
    void tick;
    const range = rangeToParams(preset, custom);
    return {
      start: range.start,
      end: range.end,
      step: range.step,
      rateWindow: range.rateWindow,
      refetchInterval: refreshToMs(refresh),
    };
  }, [preset, custom, refresh, tick]);

  const handleManualRefresh = () => {
    setTick((t) => t + 1);
  };

  // The enable/disable toggle always renders so the dashboards can be turned on
  // straight from the "not enabled" state. The dashboards themselves fire many
  // /api/monitoring/* requests on mount, and the proxy returns 404 while the
  // dashboards are disabled server-side — so below the bar the content adapts to
  // the effective flag instead of firing a wall of 404s. This page is reachable
  // directly via the /monitoring redirect and bookmarks, so the guard lives here.
  return (
    <div className={styles.page}>
      <MonitoringEnableBar />
      <MonitoringRetentionPanel />
      {!monitoringAvailable ? (
        // Health not resolved yet: MonitoringEnableBar shows its own "checking"
        // state, so render no notice here — avoids a misleading "not enabled"
        // flash on an enabled deployment during a cold fetch. Once loaded and
        // monitoring is genuinely off, show the notice.
        !health ? null : (
          <div className={styles.empty}>
            <p className={styles.emptyTitle}>Monitoring is not enabled</p>
            <p>
              The Prometheus dashboards require the monitoring stack. Deploy it
              (Prometheus + exporters), then enable it above — or install with{' '}
              <code className={styles.code}>--with-monitoring</code>.
            </p>
          </div>
        )
      ) : (
        <>
          <div className={styles.header}>
            <div className={styles.headerLeft}>
              <DashboardTabs active={tab} onChange={setTab} />
            </div>
            <div className={styles.headerRight}>
              <TimeRangePicker preset={preset} custom={custom} onChange={setRange} />
              <RefreshControl
                interval={refresh}
                onIntervalChange={setRefresh}
                onManualRefresh={handleManualRefresh}
              />
            </div>
          </div>

          {tab === 'system' && <SystemDashboard {...dashboardProps} />}
          {tab === 'database' && <DatabaseDashboard {...dashboardProps} />}
          {tab === 'redis' && <RedisDashboard {...dashboardProps} />}
          {tab === 'pipeline' && <PipelineDashboard {...dashboardProps} />}
          {tab === 'host' && <HostDashboard {...dashboardProps} />}
          {tab === 'containers' && <ContainersDashboard {...dashboardProps} />}
        </>
      )}
    </div>
  );
}
