// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/components/layout/HelpNavLink.tsx
import { Link, useLocation } from 'react-router-dom';
import { helpHrefForPath } from '@/help/helpResolver';
import styles from './Sidebar.module.css';

export function HelpNavLink() {
  const location = useLocation();
  const active = location.pathname.startsWith('/help');
  const from = (location.state as { from?: string } | null)?.from ?? null;

  const to = active ? (from ?? '/dashboard') : helpHrefForPath(location.pathname);
  const state = active ? undefined : { from: location.pathname };
  const ariaLabel = active ? 'Close help' : 'Help';

  return (
    <Link
      to={to}
      state={state}
      className={`${styles.navItem} ${active ? styles.active : ''}`}
      aria-label={ariaLabel}
    >
      <span className={styles.label}>Help</span>
      <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.15)' }}>
        <circle cx="12" cy="12" r="10" />
        <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    </Link>
  );
}
