// SPDX-License-Identifier: AGPL-3.0-or-later
import { Outlet } from 'react-router-dom';
import { SettingsSubTabBar, type SettingsTab } from '@/components/settings';

const TABS: SettingsTab[] = [
  { label: 'Users', path: 'users' },
  { label: 'API Keys', path: 'api-keys' },
  { label: 'LDAP', path: 'ldap' },
  { label: 'OIDC SSO', path: 'oidc' },
  { label: 'Audit Log', path: 'audit' },
];

export function AccessGroupPage() {
  return (
    <>
      <SettingsSubTabBar tabs={TABS} />
      <Outlet />
    </>
  );
}
