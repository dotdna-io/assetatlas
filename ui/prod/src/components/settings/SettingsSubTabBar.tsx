// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import styles from './SettingsSubTabBar.module.css';

export interface SettingsTab {
  label: string;
  path: string;
}

interface SettingsSubTabBarProps {
  tabs: SettingsTab[];
  /** Optional action(s) rendered at the far right of the tab-bar row, on the
   *  same line as the tabs (e.g. a page-level "Add" button). */
  actions?: ReactNode;
}

export function SettingsSubTabBar({ tabs, actions }: SettingsSubTabBarProps) {
  return (
    <div className={styles.row}>
      <nav className={styles.bar} aria-label="Sub-section tabs">
        {tabs.map((t) => (
          <NavLink
            key={t.path}
            to={t.path}
            end
            className={({ isActive }) =>
              `${styles.tab} ${isActive ? styles.tabActive : ''}`
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}
