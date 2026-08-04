// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ReactNode } from 'react';
import styles from './SettingsRowGrid.module.css';

interface SettingsRowGridProps {
  children: ReactNode;
  // 'grid' = 2-column responsive grid (default, used by most System tabs).
  // 'stack' = full-width stacked rows. Use when individual rows need the
  // full width to display long values (e.g. URLs in System -> Security).
  layout?: 'grid' | 'stack';
}

export function SettingsRowGrid({ children, layout = 'grid' }: SettingsRowGridProps) {
  const className = layout === 'stack' ? styles.stack : styles.grid;
  return <div className={className}>{children}</div>;
}
