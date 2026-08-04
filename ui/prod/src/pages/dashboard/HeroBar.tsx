// SPDX-License-Identifier: AGPL-3.0-or-later
import { timeAgo } from '@/utils/formatters';
import styles from './dashboard.module.css';

interface HeroBarProps {
  totalAssets: number;
  lastUpdated?: string | null;
}

const HOUR_MS = 3_600_000;

// Freshness colour: green within a day, orange within three days, red beyond.
function freshnessColor(lastUpdated: string): string {
  const ageHours = (Date.now() - new Date(lastUpdated).getTime()) / HOUR_MS;
  if (ageHours <= 24) return 'var(--success)';
  if (ageHours <= 72) return 'var(--warning)';
  return 'var(--error)';
}

export function HeroBar({ totalAssets, lastUpdated }: HeroBarProps) {
  return (
    <div className={styles.hero}>
      <div className={styles.heroTopline}>
        <span className={styles.heroNum}>{totalAssets.toLocaleString()}</span>
        <span className={styles.heroSub}>Total Assets</span>
      </div>
      {lastUpdated ? (
        <div className={styles.heroUpdated} style={{ color: freshnessColor(lastUpdated) }}>
          <span className={styles.heroUpdatedDot} />
          Last update {timeAgo(lastUpdated)}
        </div>
      ) : null}
    </div>
  );
}

export function HeroBarSkeleton() {
  return (
    <div className={styles.hero} aria-busy="true">
      <div className={styles.heroTopline}>
        <span className={`${styles.skeleton} ${styles.skelHeroNum}`} />
        <span className={`${styles.skeleton} ${styles.skelHeroSub} ${styles.skelBlock}`} />
      </div>
      <span className={`${styles.skeleton} ${styles.skelHeroUpdated}`} />
    </div>
  );
}
