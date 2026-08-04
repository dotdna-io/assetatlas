// SPDX-License-Identifier: AGPL-3.0-or-later
import { ChangeEvent } from 'react';
import type { RefreshInterval } from './types';
import styles from './RefreshControl.module.css';

interface RefreshControlProps {
  interval: RefreshInterval;
  onIntervalChange: (interval: RefreshInterval) => void;
  onManualRefresh: () => void;
}

const OPTIONS: Array<{ id: RefreshInterval; label: string }> = [
  { id: 'off', label: 'Off' },
  { id: '10s', label: '10s' },
  { id: '30s', label: '30s' },
  { id: '1m', label: '1m' },
  { id: '5m', label: '5m' },
];

export function RefreshControl({ interval, onIntervalChange, onManualRefresh }: RefreshControlProps) {
  const handleSelect = (e: ChangeEvent<HTMLSelectElement>) => {
    onIntervalChange(e.target.value as RefreshInterval);
  };
  return (
    <div className={styles.wrap}>
      <select className={styles.select} value={interval} onChange={handleSelect}>
        {OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
      <button type="button" className={styles.refresh} onClick={onManualRefresh} aria-label="Refresh">
        ↻
      </button>
    </div>
  );
}
