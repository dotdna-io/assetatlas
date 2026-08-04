// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './InsightTile.module.css';

interface InsightTileProps {
  title: string;
  value: string | number;
  detail?: string;
  status: 'ok' | 'warning';
}

export function InsightTile({ title, value, detail, status }: InsightTileProps) {
  return (
    <div className={`${styles.tile} ${status === 'warning' ? styles.warning : styles.ok}`}>
      <span className={styles.title}>{title}</span>
      <span className={styles.value}>{value}</span>
      {detail && <span className={styles.detail}>{detail}</span>}
    </div>
  );
}
