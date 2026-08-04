// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState } from 'react';
import { ButterflyChart, type ButterflyDatum } from '@/components/charts/ButterflyChart';
import { useDashboardAssetTimeline, useDashboardCoverageTrend } from '@/api/dashboard';
import styles from './dashboard.module.css';

const RANGE_DAYS = { '7d': 7, '30d': 30, '90d': 90 } as const;
type TimeRange = keyof typeof RANGE_DAYS;

// Selectable trend metrics. "Added & removed" is backed by the asset_chronicle
// timeline; the posture-coverage metrics are backed by posture_coverage_snapshots
// (driven by the posture-rules engine). Each is a paired with/without series
// rendered by the butterfly (top = the "good" half in green, bottom = the gap in red).
interface MetricDef {
  id: string;
  label: string;
  top: string;
  bottom: string;
}

const METRICS: MetricDef[] = [
  { id: 'added_removed', label: 'Added & removed assets', top: 'New', bottom: 'Departed' },
  { id: 'srv_edr', label: 'Servers · endpoint security', top: 'With EDR', bottom: 'No EDR' },
  { id: 'ws_edr', label: 'Workstations · endpoint security', top: 'With EDR', bottom: 'No EDR' },
  { id: 'mobile_mdm', label: 'Mobile · device management', top: 'Managed', bottom: 'Unmanaged' },
  { id: 'vuln', label: 'Vulnerability scan coverage', top: 'Scanned', bottom: 'Not scanned' },
];

export function TrendCard() {
  const [metricId, setMetricId] = useState('added_removed');
  const [timeRange, setTimeRange] = useState<TimeRange>('30d');
  const days = RANGE_DAYS[timeRange];
  const isAddedRemoved = metricId === 'added_removed';

  // added_removed → chronicle timeline; everything else → posture-coverage snapshots.
  const { data: timelineRaw } = useDashboardAssetTimeline(days);
  const { data: coverageRaw } = useDashboardCoverageTrend(metricId, days, !isAddedRemoved);

  const metric = METRICS.find((m) => m.id === metricId) ?? METRICS[0];

  const data: ButterflyDatum[] = useMemo(() => {
    const raw = isAddedRemoved ? timelineRaw?.data : coverageRaw?.data;
    return Array.isArray(raw) ? raw : [];
  }, [isAddedRemoved, timelineRaw, coverageRaw]);

  return (
    <div className={`${styles.intelCard} ${styles.alertsCard} ${styles.butterflyCard}`}>
      <div className={styles.butterflyHeader}>
        <div className={styles.butterflyHeaderLeft}>
          <span className={styles.trendPicker}>
            <svg
              className={styles.trendCaret}
              width="15" height="15" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"
              aria-hidden
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
            <select
              className={styles.trendSelect}
              value={metricId}
              onChange={(e) => setMetricId(e.target.value)}
              aria-label="Trend metric"
            >
              {METRICS.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </select>
          </span>
          {/* Legend beside the title. Green = the "covered/good" half (metric.top,
              e.g. With EDR / Managed / Scanned / New), red = the gap (metric.bottom).
              added_removed additionally has an amber "Returned" series between them. */}
          <div className={styles.butterflyLegend} data-testid="butterfly-legend">
            <span className={styles.butterflyLegendItem}>
              <span className={styles.butterflyLegendDot} style={{ background: 'var(--success)' }} />{metric.top}
            </span>
            {isAddedRemoved && (
              <span className={styles.butterflyLegendItem}>
                <span className={styles.butterflyLegendDot} style={{ background: 'var(--warning)' }} />Returned
              </span>
            )}
            <span className={styles.butterflyLegendItem}>
              <span className={styles.butterflyLegendDot} style={{ background: 'var(--error)' }} />{metric.bottom}
            </span>
          </div>
        </div>
        <div className={styles.inlineTimePicker}>
          {(['7d', '30d', '90d'] as const).map((opt, idx, arr) => (
            <span key={opt} className={styles.inlineTimePickerSlot}>
              <button
                type="button"
                className={`${styles.inlineTimePickerOpt} ${timeRange === opt ? styles.inlineTimePickerOptActive : ''}`}
                onClick={() => setTimeRange(opt)}
              >
                {opt}
              </button>
              {idx < arr.length - 1 && (
                <span className={styles.inlineTimePickerSep} aria-hidden>|</span>
              )}
            </span>
          ))}
        </div>
      </div>
      <div className={styles.butterflyBody}>
        {data.length > 0 ? (
          <ButterflyChart
            data={data}
            topLabel={metric.top}
            bottomLabel={metric.bottom}
            returnedLabel={isAddedRemoved ? 'Returned' : undefined}
            scaleMode={isAddedRemoved ? 'split' : 'shared'}
          />
        ) : (
          <div className={styles.chartEmpty}>No data available</div>
        )}
      </div>
    </div>
  );
}
