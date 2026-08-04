// SPDX-License-Identifier: AGPL-3.0-or-later
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { useDarkMode } from '@/hooks';
import { HelpNavLink } from './HelpNavLink';
import styles from './Sidebar.module.css';

interface NavItem {
  label: string;
  path: string;
  icon: React.ReactNode;
  /** Admin-only destination. Shown to everyone, but inert for standard users. */
  adminOnly?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    label: 'Home',
    path: '/dashboard',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.1)' }}>
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      </svg>
    ),
  },
  {
    label: 'Explore',
    path: '/explorer',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.1)' }}>
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
    ),
  },
  {
    label: 'Reporting',
    path: '/reporting',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.2)' }}>
        <line x1="5" y1="21" x2="5" y2="13" strokeWidth="3" />
        <line x1="12" y1="21" x2="12" y2="3" strokeWidth="3" />
        <line x1="19" y1="21" x2="19" y2="10" strokeWidth="3" />
      </svg>
    ),
  },
  {
    label: 'Alerts',
    path: '/alerts',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.15)' }}>
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
    ),
  },
  {
    label: 'Rules',
    path: '/rules',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.2)' }}>
        <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
        <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
      </svg>
    ),
  },
  {
    label: 'Hub',
    path: '/integrations',
    adminOnly: true,
    icon: (
      <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.2)' }}>
        <rect x="3" y="3" width="7" height="7" />
        <rect x="14" y="3" width="7" height="7" />
        <rect x="14" y="14" width="7" height="7" />
        <rect x="3" y="14" width="7" height="7" />
      </svg>
    ),
  },
];

const SETTINGS_ITEM: NavItem = {
  label: 'Settings',
  path: '/settings',
  adminOnly: true,
  icon: (
    <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.2)' }}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
};

function isActive(itemPath: string, locationPath: string): boolean {
  if (itemPath === '/dashboard') {
    return locationPath === '/' || locationPath === '/dashboard';
  }
  return locationPath.startsWith(itemPath);
}

function NavLink({
  item,
  active,
  muted = false,
}: {
  item: NavItem;
  active: boolean;
  muted?: boolean;
}) {
  // A span, not a Link with its click suppressed: a Link stays
  // keyboard-focusable and still announces itself as a link, promising
  // navigation that will not happen. Pointer events stay on so the title
  // tooltip can explain why.
  if (muted) {
    return (
      <span
        className={`${styles.navItem} ${styles.navItemMuted}`}
        aria-disabled="true"
        title={`${item.label} requires administrator access`}
      >
        <span className={styles.label}>{item.label}</span>
        {item.icon}
      </span>
    );
  }

  return (
    <Link
      to={item.path}
      className={`${styles.navItem} ${active ? styles.active : ''}`}
      aria-current={active ? 'page' : undefined}
    >
      <span className={styles.label}>{item.label}</span>
      {item.icon}
    </Link>
  );
}

function getInitial(name: string): string {
  return name ? name.charAt(0).toUpperCase() : '?';
}

export function Sidebar() {
  const location = useLocation();
  const { user, logout, isAdmin } = useAuth();
  const { isDarkMode, toggle: toggleDarkMode } = useDarkMode();

  return (
    <nav className={styles.sidebar}>
      <div className={styles.top}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            item={item}
            active={isActive(item.path, location.pathname)}
            muted={item.adminOnly === true && !isAdmin}
          />
        ))}
        <NavLink
          item={SETTINGS_ITEM}
          active={isActive(SETTINGS_ITEM.path, location.pathname)}
          muted={SETTINGS_ITEM.adminOnly === true && !isAdmin}
        />
      </div>

      <div className={styles.bottom}>
        <HelpNavLink />
        <div className={styles.profileIcon}>
          <div className={styles.profileMenu}>
            <div className={styles.profileHeader}>
              <span className={styles.profileName}>{user?.full_name || user?.username || 'User'}</span>
              <span className={styles.profileEmail}>{user?.email || ''}</span>
            </div>
            <div className={styles.profileDivider} />
            <Link to="/account" className={styles.profileMenuItem}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              <span>Profile</span>
            </Link>
            <button className={styles.profileMenuItem} onClick={toggleDarkMode} type="button">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                {isDarkMode ? (
                  <>
                    <circle cx="12" cy="12" r="5" />
                    <line x1="12" y1="1" x2="12" y2="3" />
                    <line x1="12" y1="21" x2="12" y2="23" />
                    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                    <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                    <line x1="1" y1="12" x2="3" y2="12" />
                    <line x1="21" y1="12" x2="23" y2="12" />
                    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                    <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                  </>
                ) : (
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                )}
              </svg>
              <span>{isDarkMode ? 'Light Mode' : 'Dark Mode'}</span>
            </button>
            <div className={`${styles.profileDivider} ${styles.profileDividerSpaced}`} />
            <button className={styles.profileMenuItem} onClick={() => void logout()} type="button">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              <span>Sign Out</span>
            </button>
          </div>
          <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'scale(1.2)' }}>
            <circle cx="12" cy="12" r="10" />
            <text x="12" y="16" textAnchor="middle" fontSize="12" fill="currentColor" stroke="none">{getInitial(user?.full_name || user?.username || '')}</text>
          </svg>
        </div>
      </div>
    </nav>
  );
}
