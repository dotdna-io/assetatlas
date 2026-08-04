// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import styles from './RotatingPie.module.css';

export interface RotatingPieSlice {
  label: string;
  value: number;
  color: string;
  /** Pulled out of the pie once the entrance spin settles. */
  emphasized?: boolean;
}

export interface RotatingPieProps {
  slices: RotatingPieSlice[];
  /** Diameter in px. */
  size?: number;
  animate?: boolean;
  /** Makes slices clickable (e.g. to drive a filter). */
  onSliceClick?: (slice: RotatingPieSlice) => void;
  /** Label of the currently selected slice — the others render dimmed. */
  selectedLabel?: string | null;
}

const SPIN_MS = 900;
const POP_MS = 280;
/** How far emphasized slices translate out along their mid-angle. */
const POP_DISTANCE = 7;

type Phase = 'init' | 'spin' | 'pop' | 'done';

interface ComputedSlice extends RotatingPieSlice {
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

function polarToCartesian(cx: number, cy: number, radius: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
}

/** Filled wedge from startAngle to endAngle (degrees, 0 at 12 o'clock). */
function describeWedge(cx: number, cy: number, r: number, startAngle: number, endAngle: number): string {
  const start = polarToCartesian(cx, cy, r, startAngle);
  const end = polarToCartesian(cx, cy, r, endAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y} Z`;
}

/**
 * Filled pie divided by slice values. Entrance: the whole pie rotates 720°
 * as soon as paint completes (same feel as ConfidenceDonutGauge), then the
 * slices flagged `emphasized` translate outward along their mid-angle.
 * Per-slice details (count + pct) come up in a tooltip on hover.
 */
export function RotatingPie({
  slices,
  size = 150,
  animate = true,
  onSliceClick,
  selectedLabel = null,
}: RotatingPieProps) {
  const [phase, setPhase] = useState<Phase>(animate ? 'init' : 'done');
  const [tip, setTip] = useState<Tip | null>(null);

  const signature = slices.map((s) => `${s.label}:${s.value}`).join('|');

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
    // Re-run the entrance when the data itself changes, not the array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, signature]);

  const handleSliceMove = useCallback((e: React.MouseEvent, s: ComputedSlice) => {
    const EST_HEIGHT = 80;
    const EST_WIDTH = 160;
    const halfW = EST_WIDTH / 2;
    const below = e.clientY < EST_HEIGHT + 12;
    const clampedX = Math.min(Math.max(e.clientX, halfW + 10), window.innerWidth - halfW - 10);
    setTip({
      x: clampedX,
      y: e.clientY,
      label: s.label,
      pct: Math.round(s.pct * 100),
      count: s.value,
      color: s.color,
      below,
    });
  }, []);
  const handleSliceLeave = useCallback(() => setTip(null), []);

  const total = slices.reduce((sum, s) => sum + s.value, 0);
  const cx = size / 2;
  const cy = size / 2;
  // Emphasized slices travel POP_DISTANCE outward — keep them inside the box.
  const radius = size / 2 - POP_DISTANCE - 2;

  let cumAngle = 0;
  const computed: ComputedSlice[] = slices
    .filter((s) => s.value > 0)
    .map((s) => {
      const pct = total > 0 ? s.value / total : 0;
      const start = cumAngle;
      const end = cumAngle + pct * 360;
      const mid = (start + end) / 2;
      cumAngle = end;
      return { ...s, pct, start, end, mid };
    });

  const popped = phase === 'pop' || phase === 'done';
  // Keyframe-driven spin (not a transition): a transition from a just-mounted
  // rotate(-720deg) start state races first paint and usually snaps straight
  // to 0 with no visible motion. Keyframes always play from their `from`.
  const spinning = animate && (phase === 'init' || phase === 'spin');

  return (
    <div className={styles.wrap} style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} style={{ overflow: 'visible' }}>
        {/* Empty state: faint placeholder disc. */}
        {computed.length === 0 && (
          <circle cx={cx} cy={cy} r={radius} fill="var(--bg-secondary)" opacity={0.4} />
        )}

        {/* Spinning group */}
        <g
          className={spinning ? styles.spin : undefined}
          style={{ transformOrigin: `${cx}px ${cy}px` }}
        >
          {computed.map((s) => {
            const rad = ((s.mid - 90) * Math.PI) / 180;
            const dist = popped && s.emphasized ? POP_DISTANCE : 0;
            const dimmed = selectedLabel != null && s.label !== selectedLabel;
            const sliceStyle = {
              transform: `translate(${Math.cos(rad) * dist}px, ${Math.sin(rad) * dist}px)`,
              transition: `transform ${POP_MS}ms cubic-bezier(0.34, 1.56, 0.64, 1), opacity 150ms ease`,
              opacity: dimmed ? 0.35 : 1,
              cursor: onSliceClick ? 'pointer' : 'default',
            };
            // A single populated slice fills the whole pie (360°). Drawn as one
            // SVG wedge its arc endpoints coincide and the browser omits it, so
            // render it as a full disc instead (same fix as ConfidenceDonutGauge).
            const isFullCircle = s.end - s.start >= 359.999;
            return isFullCircle ? (
              <circle
                key={s.label}
                cx={cx}
                cy={cy}
                r={radius}
                fill={s.color}
                style={sliceStyle}
                onMouseMove={(e) => handleSliceMove(e, s)}
                onMouseLeave={handleSliceLeave}
                onClick={onSliceClick ? () => onSliceClick(s) : undefined}
              />
            ) : (
              <path
                key={s.label}
                d={describeWedge(cx, cy, radius, s.start, s.end)}
                fill={s.color}
                style={sliceStyle}
                onMouseMove={(e) => handleSliceMove(e, s)}
                onMouseLeave={handleSliceLeave}
                onClick={onSliceClick ? () => onSliceClick(s) : undefined}
              />
            );
          })}
        </g>
      </svg>

      {/* Portaled: glossy cards' backdrop-filter makes the card the containing
          block and stacking context for fixed descendants — inline, the
          tooltip paints under the neighbouring card. */}
      {tip && createPortal(
        <div
          data-testid="pie-tooltip"
          className={`${styles.tooltip} ${tip.below ? styles.tooltipBelow : ''}`}
          style={{ left: tip.x, top: tip.y }}
        >
          <span className={styles.tooltipLabel}>
            <span className={styles.tooltipDot} style={{ background: tip.color }} />
            {tip.label}
          </span>
          <span className={styles.tooltipDetail}>
            {tip.count.toLocaleString()} · {tip.pct}%
          </span>
        </div>,
        document.body,
      )}
    </div>
  );
}
