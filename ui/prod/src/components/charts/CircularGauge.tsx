// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import styles from './CircularGauge.module.css';

export interface CircularGaugeProps {
  /** Label shown inside the ring (e.g., "Visibility"). */
  label: string;
  /** Level — drives fill amount, color, and the level text shown after animation. */
  level: 'low' | 'medium' | 'high';
  /** Diameter in px. */
  size?: number;
  animate?: boolean;
}

const LEVEL = {
  low:    { pct: 28, color: '#ef4444', text: 'LOW' },
  medium: { pct: 58, color: '#f59e0b', text: 'MED' },
  high:   { pct: 88, color: '#10b981', text: 'HIGH' },
};

const ANIMATION_DURATION = 1000;

/**
 * Single-value circular gauge — donut ring with a partial colored fill on a
 * neutral track. The label sits in the top half inside the ring; the level
 * (LOW/MED/HIGH) fades in below the label after the fill animation completes.
 *
 * Visual language adapted from sap-vuln-reports/dotdna_charts_react.
 */
export function CircularGauge({ label, level, size = 96, animate = true }: CircularGaugeProps) {
  const [progress, setProgress] = useState(animate ? 0 : 1);
  const [showLevel, setShowLevel] = useState(!animate);

  useEffect(() => {
    if (!animate) {
      setProgress(1);
      setShowLevel(true);
      return;
    }
    const start = Date.now();
    let frameId: number;
    const tick = () => {
      const t = Math.min((Date.now() - start) / ANIMATION_DURATION, 1);
      setProgress(1 - Math.pow(1 - t, 3)); // ease-out cubic
      if (t < 1) frameId = requestAnimationFrame(tick);
    };
    frameId = requestAnimationFrame(tick);
    const reveal = setTimeout(() => setShowLevel(true), ANIMATION_DURATION + 50);
    return () => { cancelAnimationFrame(frameId); clearTimeout(reveal); };
  }, [animate, level]);

  const { pct, color, text } = LEVEL[level];
  // Slim ring (further reduced ~25% from prior).
  const stroke = Math.max(5, size * 0.075);
  const radius = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = Math.PI * 2 * radius;
  const fillLength = circumference * (pct / 100) * progress;

  const labelFont = Math.max(10, size * 0.105);
  const levelFont = Math.max(13, size * 0.165);

  return (
    <div className={styles.wrap} style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} style={{ overflow: 'visible' }}>
        {/* Track */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke="var(--bg-secondary)"
          strokeWidth={stroke}
          opacity={0.7}
        />
        {/* Fill */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${fillLength} ${circumference}`}
          transform={`rotate(-90 ${cx} ${cy})`}
        />
        {/* Label — inside, top half. Same uppercase + tertiary-gray styling
            as the section title outside the card ("Sources per category"). */}
        <text
          x={cx}
          y={cy - labelFont * 0.55}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={labelFont}
          fontWeight={700}
          fill="var(--text-tertiary)"
          style={{
            fontFamily: 'var(--font-family)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          {label.toUpperCase()}
        </text>
        {/* Level — inside, below label, fades in after fill animation */}
        <text
          x={cx}
          y={cy + levelFont * 0.55}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={levelFont}
          fontWeight={700}
          fill={color}
          style={{
            fontFamily: 'var(--font-family)',
            letterSpacing: '0.05em',
            opacity: showLevel ? 1 : 0,
            transition: 'opacity 0.4s ease',
          }}
        >
          {text}
        </text>
      </svg>
    </div>
  );
}
