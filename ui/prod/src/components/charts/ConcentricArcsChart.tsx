// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useId, useState } from 'react';
import styles from './ConcentricArcsChart.module.css';

export interface ConcentricArc {
  /** Display label, shown in front of the arc start. */
  label: string;
  /** 0–100 — how far around the circle the colored portion fills. */
  value: number;
  /** Hex color for the filled portion. */
  color: string;
}

interface ConcentricArcsChartProps {
  arcs: ConcentricArc[]; // outer to inner
  /** SVG viewBox edge in px. Container width is independent (CSS-controlled). */
  size?: number;
}

/**
 * Concentric arcs (12 o'clock → ~9 o'clock clockwise, ~270deg sweep) with a
 * colored fill proportional to value. Arc 0 is outermost; subsequent arcs
 * nest inside. Animates the fill on mount via stroke-dasharray transition.
 *
 * Labels are rendered as HTML (not SVG text) so they inherit the app font
 * and align with each arc's start point at consistent pixel sizing.
 */
export function ConcentricArcsChart({ arcs, size = 170 }: ConcentricArcsChartProps) {
  const id = useId();
  const [animated, setAnimated] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setAnimated(true), 80);
    return () => clearTimeout(t);
  }, []);

  const cx = size / 2;
  const cy = size / 2;
  const strokeWidth = Math.max(5, size * 0.045);
  const gap = strokeWidth * 1.6;
  const outerR = size / 2 - strokeWidth - 4;

  return (
    <div className={styles.wrap}>
      {/* SVG: arcs only */}
      <svg viewBox={`0 0 ${size} ${size}`} className={styles.svg}>
        {arcs.map((arc, i) => {
          const radius = outerR - i * (strokeWidth + gap);
          if (radius < strokeWidth) return null;

          const circumference = Math.PI * 2 * radius;
          const fullSweep = 0.75; // 270deg of available track
          const trackLength = circumference * fullSweep;
          const fillLength = trackLength * (Math.max(0, Math.min(100, arc.value)) / 100);

          const startAngle = -Math.PI / 2;
          const sx = cx + radius * Math.cos(startAngle);
          const sy = cy + radius * Math.sin(startAngle);
          const endAngle = startAngle + Math.PI * 2 * fullSweep;
          const ex = cx + radius * Math.cos(endAngle);
          const ey = cy + radius * Math.sin(endAngle);
          const largeArc = fullSweep > 0.5 ? 1 : 0;
          const path = `M ${sx} ${sy} A ${radius} ${radius} 0 ${largeArc} 1 ${ex} ${ey}`;

          return (
            <path
              key={`${id}-${i}`}
              d={path}
              fill="none"
              stroke={arc.color}
              strokeWidth={strokeWidth}
              strokeLinecap="round"
              strokeDasharray={`${animated ? fillLength : 0} ${circumference}`}
              style={{ transition: 'stroke-dasharray 1s cubic-bezier(0.22, 1, 0.36, 1)' }}
            />
          );
        })}
      </svg>

      {/* HTML labels: one per arc, vertically aligned with each arc's top start.
          Computed as percentage of container height so it scales correctly. */}
      <div className={styles.labels}>
        {arcs.map((arc, i) => {
          const radius = outerR - i * (strokeWidth + gap);
          if (radius < strokeWidth) return null;
          const topPct = ((cy - radius) / size) * 100;
          return (
            <span
              key={`${id}-label-${i}`}
              className={styles.label}
              style={{
                top: `${topPct}%`,
                color: arc.color,
                opacity: animated ? 1 : 0,
                transitionDelay: `${0.15 + i * 0.12}s`,
              }}
            >
              {arc.label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
