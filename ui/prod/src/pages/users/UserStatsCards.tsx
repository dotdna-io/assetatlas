// SPDX-License-Identifier: AGPL-3.0-or-later
import type { UserStatsResponse } from '@/api/auth';
import styles from './users.module.css';

interface UserStatsCardsProps {
  stats: UserStatsResponse | undefined;
  isLoading: boolean;
}

// Inline summary strip — replaces the four-card grid that used to sit
// above the filters. The numbers carry the same semantic colours
// (success / warning / error) but the surrounding chrome (cards,
// borders, glossy surface, large typography) is gone in favour of a
// single line of `Label: value | Label: value | …` so the page leads
// with the table instead of stat cards.
const placeholder = '—';

export function UserStatsCards({ stats, isLoading }: UserStatsCardsProps) {
  const total = isLoading ? placeholder : (stats?.total ?? 0);
  const active = isLoading ? placeholder : (stats?.active ?? 0);
  const admins = isLoading ? placeholder : (stats?.by_role?.admin ?? 0);
  const inactive = isLoading ? placeholder : (stats?.inactive ?? 0);

  const items: Array<{ key: string; label: string; value: number | string; cls?: string }> = [
    { key: 'total', label: 'Total Users', value: total },
    { key: 'active', label: 'Active', value: active, cls: styles.statSuccess },
    { key: 'admins', label: 'Admins', value: admins, cls: styles.statWarning },
    { key: 'inactive', label: 'Inactive', value: inactive, cls: styles.statDanger },
  ];

  return (
    <div className={styles.statsLine}>
      {items.map((it, i) => (
        <span key={it.key} className={styles.statItem}>
          {i > 0 && <span className={styles.statSep} aria-hidden>|</span>}
          <span className={styles.statKey}>{it.label}:</span>
          <span className={`${styles.statValue} ${it.cls ?? ''}`}>{it.value}</span>
        </span>
      ))}
    </div>
  );
}
