// SPDX-License-Identifier: AGPL-3.0-or-later
import { Outlet } from 'react-router-dom';
import { SettingsSubTabBar, type SettingsTab } from '@/components/settings';

const TABS: SettingsTab[] = [
  { label: 'Configuration', path: 'configuration' },
  { label: 'Behavior', path: 'behavior' },
  { label: 'Usage', path: 'usage' },
  { label: 'Teams', path: 'teams' },
];

export function ChatbotGroupPage() {
  return (
    <>
      <SettingsSubTabBar tabs={TABS} />
      <Outlet />
    </>
  );
}
