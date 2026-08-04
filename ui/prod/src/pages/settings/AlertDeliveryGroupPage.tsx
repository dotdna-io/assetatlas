// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, type ReactNode } from 'react';
import { Outlet } from 'react-router-dom';
import { SettingsSubTabBar, type SettingsTab } from '@/components/settings';

const TABS: SettingsTab[] = [
  { label: 'Channels', path: 'channels' },
  { label: 'Routing', path: 'routing' },
];

/** A child tab (e.g. Channels) can hang a page-level action on the sub-tab
 *  row by calling setTabActions; it clears the slot on unmount. */
export interface AlertDeliveryOutletContext {
  setTabActions: (actions: ReactNode) => void;
}

export function AlertDeliveryGroupPage() {
  const [tabActions, setTabActions] = useState<ReactNode>(null);
  return (
    <>
      <SettingsSubTabBar tabs={TABS} actions={tabActions} />
      <Outlet context={{ setTabActions } satisfies AlertDeliveryOutletContext} />
    </>
  );
}
