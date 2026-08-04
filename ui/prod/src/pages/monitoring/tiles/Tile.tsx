// SPDX-License-Identifier: AGPL-3.0-or-later
import { ReactNode } from 'react';
import styles from './Tile.module.css';

interface TileProps {
  title: string;
  isLoading: boolean;
  error: unknown;
  isEmpty: boolean;
  onRetry: () => void;
  children: ReactNode;
}

export function Tile({ title, isLoading, error, isEmpty, onRetry, children }: TileProps) {
  return (
    <div className={`${styles.tile} ${error ? styles.tileError : ''}`}>
      <div className={styles.title}>{title}</div>
      <div className={styles.body}>
        {isLoading && <div data-testid="tile-skeleton" className={styles.skeleton} />}
        {!isLoading && error && (
          <div className={styles.errorBox}>
            <span>Failed to load</span>
            <button
              type="button"
              className={styles.retryButton}
              onClick={onRetry}
            >
              Retry
            </button>
          </div>
        )}
        {!isLoading && !error && isEmpty && (
          <span className={styles.empty}>No data for this range</span>
        )}
        {!isLoading && !error && !isEmpty && children}
      </div>
    </div>
  );
}
