// SPDX-License-Identifier: AGPL-3.0-or-later
import { Outlet } from 'react-router-dom';
import { SettingsSubTabBar, type SettingsTab } from '@/components/settings';

const TABS: SettingsTab[] = [
  { label: 'Retention', path: 'retention' },
  { label: 'Security', path: 'security' },
  { label: 'Logging', path: 'logging' },
  { label: 'Email', path: 'email' },
  { label: 'About', path: 'about' },
];

export function SystemGroupPage() {
  return (
    <>
      <SettingsSubTabBar tabs={TABS} />
      <Outlet />
    </>
  );
}
