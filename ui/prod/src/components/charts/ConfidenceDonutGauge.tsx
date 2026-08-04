// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import styles from './ConfidenceDonutGauge.module.css';

export interface ConfidenceDonutGaugeProps {
  /** Distribution counts. */
  high: number;
  medium: number;
  low: number;
  /** Average confidence score (0–100). Used to derive the centre level (LOW/MED/HIGH). */
  avg?: number | null;
  /** Diameter in px. */
  size?: number;
  animate?: boolean;
}

const COLORS = {
  high:   '#10b981',
  medium: '#f59e0b',
  low:    '#ef4444',
} as const;

const LABELS = {
  high:   'HIGH',
  medium: 'MED',
  low:    'LOW',
} as const;

const SPIN_MS = 900;
const POP_MS = 280;

function levelFor(avg: number): { text: 'LOW' | 'MED' | 'HIGH'; color: string } {
  if (avg >= 65) return { text: 'HIGH', color: COLORS.high };
  if (avg >= 40) return { text: 'MED',  color: COLORS.medium };
  return { text: 'LOW', color: COLORS.low };
}

function polarToCartesian(cx: number, cy: number, radius: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
}

function describeArc(cx: number, cy: number, radius: number, startAngle: number, endAngle: number): string {
  const start = polarToCartesian(cx, cy, radius, endAngle);
  const end = polarToCartesian(cx, cy, radius, startAngle);
  const largeArc = endAngle - startAngle <= 180 ? '0' : '1';
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 0 ${end.x} ${end.y}`;
}

type Phase = 'init' | 'spin' | 'pop' | 'done';

interface Slice {
  key: 'high' | 'medium' | 'low';
  color: string;
  label: 'HIGH' | 'MED' | 'LOW';
  value: number;
  pct: number;
  start: number;
  end: number;
  mid: number;
}

interface Tip {
  x: number;
  y: number;
  label: string;
  pct: number;
  count: number;
  color: string;
  below?: boolean;
}

/**
 * Donut ring divided into three colored arcs sized by the high/med/low
 * distribution. Centre shows LEVEL (in level colour) + "confidence".
 * Per-slice details (count + pct) come up in a tooltip on hover —
 * inline labels removed because they collide with each other on tiny
 * slices and clip past the column edge.
 *
 * Animation:
 *   1. init → spin — full ring rotates 720° as soon as paint completes
 *   2. spin → pop  — every slice translates outward 4px along its mid-angle
 *   3. pop → done  — centre LEVEL + "confidence" text fades in
 */
export function ConfidenceDonutGauge({
  high,
  medium,
  low,
  avg,
  size = 170,
  animate = true,
}: ConfidenceDonutGaugeProps) {
  const [phase, setPhase] = useState<Phase>(animate ? 'init' : 'done');
  const [tip, setTip] = useState<Tip | null>(null);

  useEffect(() => {
    if (!animate) {
      setPhase('done');
      return;
    }
    setPhase('init');
    const raf = requestAnimationFrame(() => setPhase('spin'));
    const t1 = setTimeout(() => setPhase('pop'), SPIN_MS + 16);
    const t2 = setTimeout(() => setPhase('done'), SPIN_MS + POP_MS + 16);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [animate, high, medium, low]);

  const handleSliceMove = useCallback(
    (e: React.MouseEvent, s: Slice) => {
      const EST_HEIGHT = 90;
      const EST_WIDTH = 170;
      const halfW = EST_WIDTH / 2;
      const below = e.clientY < EST_HEIGHT + 12;
      const clampedX = Math.min(
        Math.max(e.clientX, halfW + 10),
        window.innerWidth - halfW - 10,
      );
      setTip({
        x: clampedX,
        y: e.clientY,
        label: s.label,
        pct: Math.round(s.pct * 100),
        count: s.value,
        color: s.color,
        below,
      });
    },
    [],
  );
  const handleSliceLeave = useCallback(() => setTip(null), []);

  const total = high + medium + low;
  // Slimmer ring than before (was 10% of size) — user wants the line less fat.
  const stroke = Math.max(6, size * 0.06);
  const radius = (size - stroke) / 2 - 6;
  const cx = size / 2;
  const cy = size / 2;

  const rawSlices = [
    { key: 'high'   as const, color: COLORS.high,   label: LABELS.high,   value: high },
    { key: 'medium' as const, color: COLORS.medium, label: LABELS.medium, value: medium },
    { key: 'low'    as const, color: COLORS.low,    label: LABELS.low,    value: low },
  ].filter((s) => s.value > 0);

  let cumAngle = 0;
  const slices: Slice[] = rawSlices.map((s) => {
    const pct = total > 0 ? s.value / total : 0;
    const start = cumAngle;
    const end = cumAngle + pct * 360;
    const mid = (start + end) / 2;
    cumAngle = end;
    return { ...s, pct, start, end, mid };
  });

  // Minimal pop — the user wants barely-perceptible separation, not a
  // dramatic explode-out.
  const popDistance = (phase === 'pop' || phase === 'done') ? 4 : 0;

  const levelFont = Math.max(20, size * 0.16);
  const subFont = Math.max(10, size * 0.07);
  const centreLevel = avg != null ? levelFor(avg) : null;

  const showCentre = phase === 'done';
  const rotateAngle = phase === 'init' ? -720 : 0;

  return (
    <div className={styles.wrap} style={{ width: size }}>
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        style={{ overflow: 'visible' }}
      >
        {/* Track ring */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke="var(--bg-secondary)"
          strokeWidth={stroke}
          opacity={0.35}
        />

        {/* Spinning group */}
        <g
          style={{
            transformOrigin: `${cx}px ${cy}px`,
            transform: `rotate(${rotateAngle}deg)`,
            transition: `transform ${SPIN_MS}ms cubic-bezier(0.22, 1, 0.36, 1)`,
          }}
        >
          {slices.map((s) => {
            const rad = ((s.mid - 90) * Math.PI) / 180;
            const dx = Math.cos(rad) * popDistance;
            const dy = Math.sin(rad) * popDistance;
            const sliceStyle = {
              transform: `translate(${dx}px, ${dy}px)`,
              transition: `transform ${POP_MS}ms cubic-bezier(0.34, 1.56, 0.64, 1)`,
              cursor: 'pointer',
            };
            // A single populated band fills the whole ring (360°). Drawn as one
            // SVG arc its endpoints coincide and the browser omits it, so render
            // it as a full circle instead.
            const isFullCircle = s.end - s.start >= 359.999;
            return isFullCircle ? (
              <circle
                key={s.key}
                cx={cx}
                cy={cy}
                r={radius}
                fill="none"
                stroke={s.color}
                strokeWidth={stroke}
                style={sliceStyle}
                onMouseMove={(e) => handleSliceMove(e, s)}
                onMouseLeave={handleSliceLeave}
              />
            ) : (
              <path
                key={s.key}
                d={describeArc(cx, cy, radius, s.start, s.end)}
                fill="none"
                stroke={s.color}
                strokeWidth={stroke}
                strokeLinecap="butt"
                style={sliceStyle}
                onMouseMove={(e) => handleSliceMove(e, s)}
                onMouseLeave={handleSliceLeave}
              />
            );
          })}
        </g>

        {/* Centre — LEVEL (top, level colour) + "confidence" (bottom, muted). */}
        {centreLevel && (
          <>
            <text
              x={cx}
              y={cy - 4}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={levelFont}
              fontWeight={800}
              fill={centreLevel.color}
              style={{
                fontFamily: 'var(--font-family)',
                letterSpacing: '0.06em',
                opacity: showCentre ? 1 : 0,
                transition: 'opacity 0.4s ease',
              }}
            >
              {centreLevel.text}
            </text>
            <text
              x={cx}
              y={cy + levelFont * 0.62}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={subFont}
              fontWeight={600}
              fill="var(--text-tertiary)"
              style={{
                fontFamily: 'var(--font-family)',
                letterSpacing: '0.05em',
                opacity: showCentre ? 1 : 0,
                transition: 'opacity 0.4s ease 0.1s',
              }}
            >
              confidence
            </text>
          </>
        )}

        {/* Empty state */}
        {total === 0 && (
          <text
            x={cx}
            y={cy}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={subFont}
            fontWeight={600}
            fill="var(--text-tertiary)"
            style={{ fontFamily: 'var(--font-family)' }}
          >
            No data
          </text>
        )}
      </svg>

      {/* Portaled: the gauge sits inside glossy cards whose backdrop-filter
          makes the card the containing block and stacking context for fixed
          descendants — inline, the tooltip paints under the neighbour card. */}
      {tip && createPortal(
        <div
          data-testid="gauge-tooltip"
          className={`${styles.tooltip} ${tip.below ? styles.tooltipBelow : ''}`}
          style={{ left: tip.x, top: tip.y }}
        >
          <span className={styles.tooltipRow}>
            <span className={styles.tooltipDot} style={{ background: tip.color }} />
            <span style={{ color: tip.color, fontWeight: 700 }}>{tip.label}</span>
          </span>
          <span className={styles.tooltipCount}>
            {tip.count.toLocaleString()} <span className={styles.tooltipPct}>({tip.pct}%)</span>
          </span>
        </div>,
        document.body,
      )}
    </div>
  );
}
