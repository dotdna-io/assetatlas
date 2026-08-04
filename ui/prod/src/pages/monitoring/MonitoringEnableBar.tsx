// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { settingsApi, useHealth } from '@/api/settings';
import { useMonitoringStatus } from '@/api/monitoring';
import { getApiErrorMessage } from '@/api/client';
import { InlineStatus, type InlineStatusValue } from '@/components/ui/InlineStatus';
import styles from './MonitoringEnableBar.module.css';

/**
 * Two layers of monitoring control, kept visually distinct so the difference is
 * obvious:
 *
 * 1. App-level toggle (`monitoring.dashboards` setting) — shows/hides the
 *    dashboards and serves/404s the /api/monitoring proxy. Instant, no restart.
 *    Does NOT start or stop the Prometheus containers.
 * 2. The stack itself — the Prometheus + exporter containers. Their lifecycle is
 *    a host/ops action (`assetatlas-cli enable|disable monitoring`); a web app
 *    can't safely start/stop host containers. We show the live status (via the
 *    reachability probe) and the exact commands to run.
 */
export function MonitoringEnableBar() {
  const qc = useQueryClient();
  const { data: health } = useHealth();
  // Defer the reachability probe (a ping with a 10s timeout) until health
  // resolves — while health is loading the bar renders only its placeholder, so
  // the stack status isn't shown yet.
  const stack = useMonitoringStatus(!!health);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<InlineStatusValue | null>(null);

  // Until health resolves we can't tell enabled from disabled — show a neutral
  // placeholder rather than an unchecked box that would imply "off" on an
  // enabled deployment during a cold fetch (mirrors MonitoringPage's guard).
  if (!health) {
    return (
      <div className={styles.bar}>
        <span className={styles.loading}>Checking monitoring status…</span>
      </div>
    );
  }

  const enabled = health.application?.features?.monitoring === true;
  const stackRunning = stack.data?.prometheus_reachable === true;
  const stackState = stack.isLoading
    ? 'Checking…'
    : stackRunning
      ? 'Running'
      : 'Stopped or not deployed';

  async function handleToggle() {
    setSaving(true);
    setStatus(null);
    try {
      await settingsApi.updateSetting('monitoring', 'dashboards', {
        value: enabled ? 'disabled' : 'enabled',
      });
      // Only the top-level ['health'] query carries features.monitoring; use
      // exact so we don't also refetch ['health','about'] / ['health','update-check'].
      await qc.invalidateQueries({ queryKey: ['health'], exact: true });
      setStatus({
        kind: 'success',
        message: enabled ? 'Dashboards disabled.' : 'Dashboards enabled.',
      });
    } catch (err) {
      setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Failed to update setting') });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.bar}>
      {/* Layer 1 — app-level display toggle */}
      <label className={styles.toggle}>
        <input type="checkbox" checked={enabled} disabled={saving} onChange={handleToggle} />
        Enable monitoring dashboards
      </label>
      <p className={styles.hint}>
        Shows or hides the dashboards and the <code>/api/monitoring</code> proxy. This does
        not start or stop the Prometheus containers — use the stack controls below.
      </p>
      {status && <InlineStatus status={status} align="inline" />}

      {/* Layer 2 — the actual container stack (host-managed) */}
      <div className={styles.stack}>
        <div className={styles.stackHead}>
          <span
            className={`${styles.dot} ${stackRunning ? styles.dotOn : styles.dotOff}`}
            aria-hidden
          />
          <span className={styles.stackLabel}>Monitoring stack:</span>
          <span className={styles.stackState}>{stackState}</span>
          <button
            type="button"
            className={styles.refresh}
            onClick={() => stack.refetch()}
            disabled={stack.isFetching}
          >
            {stack.isFetching ? 'Checking…' : 'Refresh'}
          </button>
        </div>
        <p className={styles.hint}>
          Starting or stopping the Prometheus containers is a host action (a web app can't
          safely manage host containers). Run on the server:
        </p>
        <pre className={styles.cmd}>
{`assetatlas-cli enable monitoring    # start the stack
assetatlas-cli disable monitoring   # stop the stack`}
        </pre>
      </div>
    </div>
  );
}
