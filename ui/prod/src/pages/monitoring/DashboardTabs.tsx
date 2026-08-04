// SPDX-License-Identifier: AGPL-3.0-or-later
import type { TabId } from './types';
import styles from './DashboardTabs.module.css';

interface DashboardTabsProps {
  active: TabId;
  onChange: (tab: TabId) => void;
}

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'system', label: 'System' },
  { id: 'database', label: 'Database' },
  { id: 'redis', label: 'Redis' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'host', label: 'Host' },
  { id: 'containers', label: 'Containers' },
];

export function DashboardTabs({ active, onChange }: DashboardTabsProps) {
  return (
    <div className={styles.wrap}>
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          className={`${styles.tab} ${active === t.id ? styles.tabActive : ''}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
