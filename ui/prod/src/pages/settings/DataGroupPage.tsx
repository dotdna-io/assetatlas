// SPDX-License-Identifier: AGPL-3.0-or-later
import { Outlet } from 'react-router-dom';
import { SettingsSubTabBar, type SettingsTab } from '@/components/settings';

const TABS: SettingsTab[] = [
  { label: 'Pipeline', path: 'pipeline' },
  { label: 'Confidence', path: 'confidence' },
  { label: 'Semantic', path: 'semantic' },
  { label: 'Enrichment', path: 'enrichment' },
  { label: 'Unified Fields', path: 'unified-fields' },
  { label: 'Reference Data', path: 'reference-data' },
  { label: 'Merge Denylist', path: 'merge-denylist' },
];

export function DataGroupPage() {
  return (
    <>
      <SettingsSubTabBar tabs={TABS} />
      <Outlet />
    </>
  );
}
