// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import styles from './PostureScoreBars.module.css';

export interface LevelInfo {
  text: 'LOW' | 'MED' | 'HIGH';
  color: string;
  pct: number;
}

export function levelFromScore(score: number): LevelInfo {
  if (score >= 65) return { text: 'HIGH', color: '#10b981', pct: 88 };
  if (score >= 40) return { text: 'MED',  color: '#f59e0b', pct: 58 };
  return { text: 'LOW', color: '#ef4444', pct: 28 };
}

const SEGMENT_COUNT = 12;
const SEGMENT_STAGGER_MS = 60;
const EMPTY_FADED_OPACITY = 0.55;
// Theme-aware token: light value on light themes, light-on-dark value
// in dark mode (defined in tokens.css / dark-mode.css). Falls back to
// a black-alpha value if the variable isn't defined.
const EMPTY_BRICK_BG = 'var(--posture-empty-brick, rgba(0, 0, 0, 0.18))';

/** Three-phase animation:
 *  1. `init`  — every segment is barely visible (opacity = EMPTY_FADED_OPACITY)
 *  2. `fill`  — cascade left → right; filled segments light up in their level color,
 *               empty segments still light up (in grey) at full opacity, all staggered
 *  3. `done`  — empty segments fade back to barely visible; filled stay lit
 */
type Phase = 'init' | 'fill' | 'done';

interface PostureScoreBarsProps {
  rows: { label: string; level: LevelInfo }[];
}

export function PostureScoreBars({ rows }: PostureScoreBarsProps) {
  const [phase, setPhase] = useState<Phase>('init');
  useEffect(() => {
    const startFill = setTimeout(() => setPhase('fill'), 120);
    const fadeEmpty = setTimeout(() => setPhase('done'), 120 + SEGMENT_COUNT * SEGMENT_STAGGER_MS + 350);
    return () => { clearTimeout(startFill); clearTimeout(fadeEmpty); };
  }, []);

  return (
    <div className={styles.scoreRows}>
      {rows.map((r) => {
        const filled = Math.round((r.level.pct / 100) * SEGMENT_COUNT);
        return (
          <div className={styles.scoreRow} key={r.label}>
            <div className={styles.scoreHeader}>
              <span className={styles.scoreLabel}>{r.label}</span>
              <span className={styles.scoreChip} style={{ color: r.level.color }}>{r.level.text}</span>
            </div>
            <div className={styles.scoreBarWrap}>
              {Array.from({ length: SEGMENT_COUNT }).map((_, i) => {
                const isFilled = i < filled;
                let opacity: number;
                if (phase === 'init') opacity = EMPTY_FADED_OPACITY;
                else if (phase === 'fill') opacity = 1;
                else opacity = isFilled ? 1 : EMPTY_FADED_OPACITY;
                const colored = phase !== 'init' && isFilled;
                return (
                  <span
                    key={i}
                    className={styles.scoreSegment}
                    style={{
                      background: colored ? r.level.color : EMPTY_BRICK_BG,
                      opacity,
                      transitionDelay: phase === 'fill' ? `${i * SEGMENT_STAGGER_MS}ms` : '0ms',
                    }}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
