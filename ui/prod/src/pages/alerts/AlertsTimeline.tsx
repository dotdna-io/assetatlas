// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState, useCallback } from 'react';
import type { Alert } from '@/api/alerts';
import { SEVERITIES, SEVERITY_COLORS, SEVERITY_LABELS } from '@/constants/severity';
import styles from './alerts-timeline.module.css';

export interface TimelineBarFilter {
  severity: string;
  /** Bucket key — see bucketKey() below. */
  date: string;
}

interface AlertsTimelineProps {
  /** Already narrowed by the page's time picker and source dropdown —
   *  the chart shows AssetAtlas + custom alerts combined. */
  alerts: Alert[];
  timeRange: string;
  customRange: { start: string; end: string } | null;
  activeFilter?: TimelineBarFilter | null;
  onSegmentClick?: (severity: string, date: string) => void;
}

interface Tip {
  x: number;
  y: number;
  label: string;
  counts: Record<string, number>;
  total: number;
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/** Local bucket key for an alert. 24h → YYYY-MM-DD HH; others → YYYY-MM-DD. */
export function bucketKey(iso: string, range: string): string {
  const d = new Date(iso);
  const ymd = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  if (range === '24h') return `${ymd} ${pad2(d.getHours())}`;
  return ymd;
}

interface Bucket {
  key: string;
  label: string;
}

function buildBuckets(range: string, customRange: { start: string; end: string } | null): Bucket[] {
  const buckets: Bucket[] = [];
  const now = new Date();

  if (range === '24h') {
    for (let h = 23; h >= 0; h--) {
      const d = new Date(now.getTime() - h * 3600000);
      const key = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}`;
      buckets.push({ key, label: `${pad2(d.getHours())}:00` });
    }
    return buckets;
  }

  if (range === 'custom' && customRange) {
    const start = new Date(customRange.start + 'T00:00:00');
    const end = new Date(customRange.end + 'T00:00:00');
    for (let t = start.getTime(); t <= end.getTime(); t += 86400000) {
      const d = new Date(t);
      const key = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
      buckets.push({ key, label: `${d.getMonth() + 1}/${d.getDate()}` });
    }
    return buckets;
  }

  const days = range === '30d' ? 30 : 7;
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now.getTime() - i * 86400000);
    const key = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
    buckets.push({ key, label: `${d.getMonth() + 1}/${d.getDate()}` });
  }
  return buckets;
}

function formatBucketLabel(key: string, range: string): string {
  if (range === '24h') {
    // key is "YYYY-MM-DD HH"
    const [ymd, hh] = key.split(' ');
    const [, mm, dd] = ymd.split('-');
    return `${parseInt(mm, 10)}/${parseInt(dd, 10)} · ${hh}:00`;
  }
  // key is "YYYY-MM-DD"
  const d = new Date(key + 'T00:00:00');
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

export function AlertsTimeline({
  alerts,
  timeRange,
  customRange,
  activeFilter,
  onSegmentClick,
}: AlertsTimelineProps) {
  const [tip, setTip] = useState<Tip | null>(null);

  const buckets = useMemo(() => buildBuckets(timeRange, customRange), [timeRange, customRange]);

  const { rows, max, grand } = useMemo(() => {
    const bucketRows = buckets.map((b) => {
      const counts: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0 };
      for (const a of alerts) {
        if (bucketKey(a.created_at, timeRange) !== b.key) continue;
        counts[a.severity] = (counts[a.severity] || 0) + 1;
      }
      const total = counts.critical + counts.high + counts.medium + counts.low;
      return { ...b, counts, total };
    });
    return {
      rows: bucketRows,
      max: Math.max(1, ...bucketRows.map((r) => r.total)),
      grand: bucketRows.reduce((sum, r) => sum + r.total, 0),
    };
  }, [alerts, buckets, timeRange]);

  const handleMove = useCallback(
    (e: React.MouseEvent, label: string, counts: Record<string, number>, total: number) => {
      setTip({ x: e.clientX, y: e.clientY, label, counts, total });
    },
    [],
  );
  const handleLeave = useCallback(() => setTip(null), []);

  return (
    <div className={styles.grid}>
      <div className={styles.card}>
        {/* No count header — the toolbar's total covers it. */}
        {grand === 0 ? (
          <div className={styles.empty}>No alerts in this period</div>
        ) : (
          <>
            <div className={styles.chart}>
              {rows.map((r, idx) => (
                <div
                  key={r.key}
                  className={styles.bar}
                  onMouseMove={(e) => handleMove(e, formatBucketLabel(r.key, timeRange), r.counts, r.total)}
                  onMouseLeave={handleLeave}
                >
                  {r.total > 0 && (
                    <div
                      className={styles.barFill}
                      style={{
                        height: `${(r.total / max) * 100}%`,
                        animationDelay: `${idx * 25}ms`,
                      }}
                    >
                      {SEVERITIES.map((sev) => {
                        const count = r.counts[sev] || 0;
                        if (count === 0) return null;
                        const isActive =
                          activeFilter?.severity === sev &&
                          activeFilter?.date === r.key;
                        return (
                          <span
                            key={sev}
                            className={`${styles.segment} ${isActive ? styles.segmentActive : ''}`}
                            style={{
                              height: `${(count / r.total) * 100}%`,
                              background: SEVERITY_COLORS[sev],
                            }}
                            onClick={(e) => {
                              e.stopPropagation();
                              onSegmentClick?.(sev, r.key);
                            }}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
            {/* X-axis ticks. Aim for ~6 visible labels regardless of range
                by thinning out longer series; first and last buckets are
                always shown so the chart's start/end are anchored. */}
            <div className={styles.axis} aria-hidden="true">
              {rows.map((r, idx) => {
                const interval = rows.length <= 7 ? 1 : Math.ceil(rows.length / 6);
                const show = idx === 0 || idx === rows.length - 1 || idx % interval === 0;
                return (
                  <span key={r.key} className={styles.axisTick}>
                    {show ? r.label : ''}
                  </span>
                );
              })}
            </div>
          </>
        )}
      </div>

      {tip && (
        <div
          className={`${styles.tooltip} ${tip.y < 190 ? styles.tooltipBelow : ''}`}
          style={{
            // Clamp X so the (~ 90px half-width) tooltip never leaves the
            // viewport; flip below the cursor when there's no room above —
            // the chart now sits near the top of the page.
            left: Math.min(Math.max(tip.x, 100), window.innerWidth - 100),
            top: tip.y,
          }}
        >
          <span className={styles.tooltipDate}>{tip.label}</span>
          {SEVERITIES.map((sev) =>
            tip.counts[sev] > 0 ? (
              <span key={sev} className={styles.tooltipRow}>
                <span className={styles.tooltipDot} style={{ background: SEVERITY_COLORS[sev] }} />
                <span>{SEVERITY_LABELS[sev]}</span>
                <span className={styles.tooltipCount}>{tip.counts[sev]}</span>
              </span>
            ) : null,
          )}
          <span className={styles.tooltipTotal}>
            <span>Total</span>
            <span className={styles.tooltipTotalCount}>{tip.total}</span>
          </span>
        </div>
      )}
    </div>
  );
}
