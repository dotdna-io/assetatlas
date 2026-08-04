// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/SettingsPage.tsx
import { useDocumentTitle } from '@/hooks';
import { NavLink, Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import styles from './SettingsPage.module.css';

const TABS = [
  { label: 'System', path: 'system' },
  { label: 'Data Pipeline', path: 'data' },
  { label: 'Access', path: 'access' },
  { label: 'Chatbot', path: 'chatbot' },
  { label: 'Alert Delivery', path: 'alert-delivery' },
  { label: 'Monitoring', path: 'monitoring' },
  // About lives under System now (was a top-level tab).
];

export default function SettingsPage() {
  useDocumentTitle('Settings');
  const { isAdmin, isLoading } = useAuth();

  // ProtectedRoute shows spinner while loading; guard against premature redirect
  if (isLoading) return null;

  // Settings is admin-only (legacy enforces via data-require-admin="true")
  if (!isAdmin) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className={styles.page}>
      <nav className={styles.tabBar}>
        {TABS.map((tab) => (
          <NavLink
            key={tab.path}
            to={tab.path}
            className={({ isActive }) =>
              `${styles.tab} ${isActive ? styles.tabActive : ''}`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
