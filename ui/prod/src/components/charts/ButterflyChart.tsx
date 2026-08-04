// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import styles from './ButterflyChart.module.css';

export interface ButterflyDatum {
  label: string;
  added: number;
  removed: number;
  /** Optional secondary top series ("returned"), stacked above `added`. */
  returned?: number;
}

interface ButterflyChartProps {
  data: ButterflyDatum[];
  /** Color for the "added" half (top). Defaults to project success green. */
  addedColor?: string;
  /** Color for the "removed" half (bottom). Defaults to project error red. */
  removedColor?: string;
  /** Color for the optional "returned" series (top, stacked). Amber. */
  returnedColor?: string;
  /** Tooltip labels for the two halves. Default "Added" / "Removed". */
  topLabel?: string;
  bottomLabel?: string;
  /** When set, enables the 3-series mode: a stacked "returned" bar + a legend. */
  returnedLabel?: string;
  /** 'split' (default): each half scales to its own max. 'shared': both halves
   *  share one scale so the top/bottom ratio is visible (coverage metrics). */
  scaleMode?: 'split' | 'shared';
  className?: string;
}

interface Tip {
  x: number;
  y: number;
  label: string;
  added: number;
  returned?: number;
  removed: number;
  below?: boolean;
}

const topTotal = (d: ButterflyDatum) => d.added + (d.returned ?? 0);

/**
 * Split-scale butterfly bar chart. Top half (added, optionally with a stacked
 * "returned" series above it) and bottom half (removed) each scale to their
 * own max so a few large days don't crush detail. When `returnedLabel` is set
 * the top half stacks New (base) + Returned (above); otherwise the chart is
 * unchanged (2 series). The legend, when needed, is rendered by the caller.
 */
export function ButterflyChart({
  data,
  addedColor = 'var(--success)',
  removedColor = 'var(--error)',
  returnedColor = 'var(--warning)',
  topLabel = 'Added',
  bottomLabel = 'Removed',
  returnedLabel,
  scaleMode = 'split',
  className,
}: ButterflyChartProps) {
  const [tip, setTip] = useState<Tip | null>(null);
  const threeSeries = returnedLabel != null;

  const maxAdded = scaleMode === 'shared'
    ? Math.max(1, ...data.flatMap((d) => [topTotal(d), d.removed]))
    : Math.max(1, ...data.map(topTotal));
  const maxRemoved = scaleMode === 'shared' ? maxAdded : Math.max(1, ...data.map((d) => d.removed));

  const handleMove = useCallback((e: React.MouseEvent, d: ButterflyDatum) => {
    const EST_HEIGHT = 110;
    const EST_WIDTH = 180;
    const halfW = EST_WIDTH / 2;
    const below = e.clientY < EST_HEIGHT + 12;
    const clampedX = Math.min(
      Math.max(e.clientX, halfW + 10),
      window.innerWidth - halfW - 10,
    );
    setTip({ x: clampedX, y: e.clientY, label: d.label, added: d.added, returned: d.returned, removed: d.removed, below });
  }, []);
  const handleLeave = useCallback(() => setTip(null), []);

  const stagger = Math.max(20, Math.min(80, Math.floor(700 / Math.max(1, data.length))));
  const startLabel = data[0]?.label ?? '';
  const endLabel = data[data.length - 1]?.label ?? '';

  return (
    <div className={`${styles.wrap} ${className ?? ''}`}>
      {/* Top half — added (base) + optional returned (stacked above) */}
      <div className={styles.topRow}>
        {data.map((d, idx) => (
          <div key={`top-${idx}`} className={styles.col}>
            {(d.added > 0 || (d.returned ?? 0) > 0) && (
              <div className={styles.stack}>
                {threeSeries && (d.returned ?? 0) > 0 && (
                  <div
                    className={styles.barReturned}
                    data-testid="bar-returned"
                    style={{
                      height: `${((d.returned ?? 0) / maxAdded) * 98}%`,
                      background: returnedColor,
                      animationDelay: `${idx * stagger}ms`,
                    }}
                    onMouseMove={(e) => handleMove(e, d)}
                    onMouseLeave={handleLeave}
                  />
                )}
                {d.added > 0 && (
                  <div
                    data-testid="bar-added"
                    className={threeSeries && (d.returned ?? 0) > 0 ? styles.barAddedStacked : styles.barAdded}
                    style={{
                      height: `${(d.added / maxAdded) * 98}%`,
                      background: addedColor,
                      animationDelay: `${idx * stagger}ms`,
                    }}
                    onMouseMove={(e) => handleMove(e, d)}
                    onMouseLeave={handleLeave}
                  />
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Centre row — start date | line | end date */}
      <span className={styles.startDate}>{startLabel}</span>
      <div className={styles.line} aria-hidden />
      <span className={styles.endDate}>{endLabel}</span>

      {/* Bottom half — removed bars grow downward */}
      <div className={styles.bottomRow}>
        {data.map((d, idx) => (
          <div key={`bot-${idx}`} className={styles.col}>
            {d.removed > 0 && (
              <div
                className={styles.barRemoved}
                style={{
                  height: `${(d.removed / maxRemoved) * 98}%`,
                  background: removedColor,
                  animationDelay: `${idx * stagger}ms`,
                }}
                onMouseMove={(e) => handleMove(e, d)}
                onMouseLeave={handleLeave}
              />
            )}
          </div>
        ))}
      </div>

      {/* Portaled: the chart sits inside glossy cards whose backdrop-filter
          turns the card into the containing block for fixed descendants, so
          an inline tooltip lands off-card and is clipped by overflow:hidden. */}
      {tip && createPortal(
        <div
          data-testid="butterfly-tooltip"
          className={`${styles.tooltip} ${tip.below ? styles.tooltipBelow : ''}`}
          style={{ left: tip.x, top: tip.y }}
        >
          <span className={styles.tooltipDate}>{tip.label}</span>
          <span className={styles.tooltipRow}>
            <span className={styles.tooltipDot} style={{ background: addedColor }} />
            <span>{topLabel}</span>
            <span className={styles.tooltipCount}>{tip.added.toLocaleString()}</span>
          </span>
          {threeSeries && (
            <span className={styles.tooltipRow}>
              <span className={styles.tooltipDot} style={{ background: returnedColor }} />
              <span>{returnedLabel}</span>
              <span className={styles.tooltipCount}>{(tip.returned ?? 0).toLocaleString()}</span>
            </span>
          )}
          <span className={styles.tooltipRow}>
            <span className={styles.tooltipDot} style={{ background: removedColor }} />
            <span>{bottomLabel}</span>
            <span className={styles.tooltipCount}>{tip.removed.toLocaleString()}</span>
          </span>
        </div>,
        document.body,
      )}
    </div>
  );
}
