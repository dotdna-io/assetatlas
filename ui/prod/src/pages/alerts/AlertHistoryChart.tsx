// SPDX-License-Identifier: AGPL-3.0-or-later
import { useAlertHistory } from '@/api/alerts';
import styles from './alert-history-chart.module.css';

/**
 * Involved-assets-over-time line chart for the alert drilldown.
 *
 * One point per pipeline run in the window (oldest → newest, capped at 90
 * days by useAlertHistory). Y encodes how many assets the alert covered on
 * that run — alerts aren't managed, so they can fire indefinitely and the
 * trend is the signal. Self-fetching and mounted only when a drilldown opens
 * for an alert with assets, so the query is naturally gated.
 */

// The viewBox stretches to the container (preserveAspectRatio "none"), so the
// units are arbitrary; PAD_Y keeps the stroke from clipping at the extremes.
const VB_W = 600;
const VB_H = 100;
const PAD_Y = 6;

export function AlertHistoryChart({ alertId }: { alertId: string }) {
  const { data, isLoading, error } = useAlertHistory(alertId);
  const history = data?.history ?? [];

  if (isLoading) {
    return (
      <div className={styles.wrap}>
        <span className={styles.title}>Assets over time</span>
        <div className={styles.empty}>Loading…</div>
      </div>
    );
  }

  // A failed request must not masquerade as "no history" (which reads as
  // "never fired") — surface the error distinctly.
  if (error) {
    return (
      <div className={styles.wrap}>
        <span className={styles.title}>Assets over time</span>
        <div className={styles.empty}>Couldn't load history</div>
      </div>
    );
  }

  if (history.length === 0) {
    return (
      <div className={styles.wrap}>
        <span className={styles.title}>Assets over time</span>
        <div className={styles.empty}>No history yet</div>
      </div>
    );
  }

  const maxCount = Math.max(1, ...history.map((p) => p.asset_count));
  const n = history.length;
  const points = history.map((p, i) => ({
    x: n === 1 ? VB_W / 2 : (i / (n - 1)) * VB_W,
    y: PAD_Y + (1 - p.asset_count / maxCount) * (VB_H - PAD_Y * 2),
    run: p,
  }));

  const lineD = points
    .map((pt, i) => `${i === 0 ? 'M' : 'L'} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`)
    .join(' ');
  const areaD =
    `${lineD} L ${points[n - 1].x.toFixed(1)} ${VB_H} L ${points[0].x.toFixed(1)} ${VB_H} Z`;

  const gradientId = `alert-history-area-${alertId}`;
  const firstRun = history[0];
  const lastRun = history[n - 1];
  const dateOf = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : '');

  return (
    <div className={styles.wrap}>
      <span className={styles.title}>Assets over time</span>
      <svg
        className={styles.chart}
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Involved assets across ${n} pipeline runs`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.18" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <path d={areaD} fill={`url(#${gradientId})`} />
        <path
          data-testid="alert-history-line"
          d={lineD}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {points.map((pt, i) => {
          const when = pt.run.started_at
            ? new Date(pt.run.started_at).toLocaleString()
            : 'unknown time';
          const count = pt.run.asset_count;
          return (
            // Invisible hit column per run: the native <title> tooltip needs a
            // hoverable area wider than a 2px line.
            <rect
              key={pt.run.pipeline_run_id ?? `idx-${i}`}
              data-testid="alert-history-point"
              x={n === 1 ? 0 : Math.max(0, pt.x - VB_W / (n - 1) / 2)}
              y={0}
              width={n === 1 ? VB_W : VB_W / (n - 1)}
              height={VB_H}
              fill="transparent"
            >
              <title>{`${when} — ${count} ${count === 1 ? 'asset' : 'assets'}`}</title>
            </rect>
          );
        })}
      </svg>
      <div className={styles.dates} data-testid="alert-history-dates" aria-hidden="true">
        <span>{dateOf(firstRun.started_at)}</span>
        <span>{dateOf(lastRun.started_at)}</span>
      </div>
    </div>
  );
}
