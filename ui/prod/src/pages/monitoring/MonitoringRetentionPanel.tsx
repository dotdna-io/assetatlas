// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { settingsApi, useHealth } from '@/api/settings';
import { useMonitoringStorage } from '@/api/monitoring';
import { getApiErrorMessage } from '@/api/client';
import { InlineStatus, type InlineStatusValue } from '@/components/ui/InlineStatus';
import styles from './MonitoringRetentionPanel.module.css';

// Single-unit presets offered in the dropdown (backend accepts any single-unit
// duration; these cover the useful range). '' = "use the deployed value".
const PRESETS: { value: string; label: string; seconds: number }[] = [
  { value: '7d', label: '7 days', seconds: 604800 },
  { value: '15d', label: '15 days', seconds: 1296000 },
  { value: '30d', label: '30 days', seconds: 2592000 },
  { value: '60d', label: '60 days', seconds: 5184000 },
  { value: '90d', label: '90 days', seconds: 7776000 },
  { value: '180d', label: '180 days', seconds: 15552000 },
  { value: '1y', label: '1 year', seconds: 31536000 },
];

function formatBytes(n: number | null): string {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  return `${(n / 1e3).toFixed(0)} KB`;
}

function formatDays(seconds: number | null): string {
  if (seconds == null) return '—';
  return `${(seconds / 86400).toFixed(1)} days`;
}

/**
 * Storage usage + desired-retention control, shown under MonitoringEnableBar in
 * Settings -> Monitoring -> Dashboards. Retention is a Prometheus startup flag,
 * so changing it is a host action: we store the desired value (an app setting),
 * show applied-vs-desired, and surface the host command to apply it.
 */
export function MonitoringRetentionPanel() {
  const qc = useQueryClient();
  const { data: health } = useHealth();
  const storage = useMonitoringStorage(!!health);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<InlineStatusValue | null>(null);

  if (!health) return null; // MonitoringEnableBar renders the shared "checking" state.

  const s = storage.data;
  const loading = storage.isLoading;
  const reachable = s?.reachable === true;
  const selected = s?.desired_retention ?? '';

  // Projection: extrapolate this deployment's real bytes/second to the selected
  // window. Uses server-parsed applied seconds when "use deployed value" (''),
  // else the preset's seconds. '—' when there isn't enough history to estimate.
  const selectedSeconds = selected
    ? PRESETS.find((p) => p.value === selected)?.seconds ?? null
    : s?.applied_retention_seconds ?? null;
  const bytesPerSecond =
    s?.disk_used_bytes != null && s?.history_seconds != null
      ? s.disk_used_bytes / s.history_seconds
      : null;
  const projected =
    bytesPerSecond != null && selectedSeconds != null ? bytesPerSecond * selectedSeconds : null;

  async function handleChange(value: string) {
    setSaving(true);
    setStatus(null);
    try {
      await settingsApi.updateSetting('monitoring', 'retention', { value });
      await qc.invalidateQueries({ queryKey: ['monitoring', 'storage'], exact: true });
      setStatus({ kind: 'success', message: 'Desired retention saved.' });
    } catch (err) {
      setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Failed to save retention') });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <span className={styles.title}>Metrics storage &amp; retention</span>
        <button
          type="button"
          className={styles.refresh}
          onClick={() => storage.refetch()}
          disabled={storage.isFetching}
        >
          {storage.isFetching ? 'Checking…' : 'Refresh'}
        </button>
      </div>

      {loading ? (
        <p className={styles.hint}>Checking monitoring storage…</p>
      ) : !reachable ? (
        <p className={styles.hint}>
          The monitoring stack is not reachable, so storage usage is unavailable. Start it from the
          stack controls above, then Refresh.
        </p>
      ) : (
        <>
          <dl className={styles.stats}>
            <div className={styles.stat}>
              <dt>Disk used</dt>
              <dd>{formatBytes(s?.disk_used_bytes ?? null)}</dd>
            </div>
            <div className={styles.stat}>
              <dt>History retained</dt>
              <dd>{formatDays(s?.history_seconds ?? null)}</dd>
            </div>
            <div className={styles.stat}>
              <dt>Ingest rate</dt>
              <dd>
                {s?.samples_per_second != null
                  ? `${Math.round(s.samples_per_second).toLocaleString()} samples/s`
                  : '—'}
              </dd>
            </div>
            <div className={styles.stat}>
              <dt>Active series</dt>
              <dd>{s?.head_series != null ? s.head_series.toLocaleString() : '—'}</dd>
            </div>
          </dl>

          <label className={styles.control}>
            <span className={styles.controlLabel}>Desired retention</span>
            <select
              className={styles.select}
              aria-label="Desired retention"
              value={selected}
              disabled={saving}
              onChange={(e) => handleChange(e.target.value)}
            >
              <option value="">
                Use deployed value{s?.applied_retention ? ` (${s.applied_retention})` : ''}
              </option>
              {PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <p className={styles.hint}>
            Estimated size at this retention: <strong>{formatBytes(projected)}</strong> (based on
            current ingest; actual varies).
          </p>
          {status && <InlineStatus status={status} align="inline" />}

          {s?.pending && (
            <div className={styles.pending}>
              <p className={styles.hint}>
                ⚠ Pending — the desired retention differs from what Prometheus is running. Changing
                retention needs a Prometheus restart, so apply it on the host:
              </p>
              <pre className={styles.cmd}>assetatlas-cli apply-retention</pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}
