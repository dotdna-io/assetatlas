// SPDX-License-Identifier: AGPL-3.0-or-later
import { Outlet } from 'react-router-dom';
import { SettingsSubTabBar, type SettingsTab } from '@/components/settings';

// Monitoring group: the metrics-forwarding config (#922) plus the Prometheus
// dashboards. Both sub-tabs are ALWAYS shown:
// - Forwarding configures remote_write regardless of a queryable Prometheus.
// - Dashboards now hosts the enable/disable toggle for the /api/monitoring proxy
//   (MonitoringEnableBar), so it must stay reachable even when the dashboards are
//   currently off — otherwise there'd be no way to turn them back on.
// (Previously Dashboards was hidden when monitoring was unavailable; the toggle
// living inside it makes an always-visible tab the natural home.)
const TABS: SettingsTab[] = [
  { label: 'Forwarding', path: 'forwarding' },
  { label: 'Dashboards', path: 'dashboards' },
];

export function MonitoringGroupPage() {
  return (
    <>
      <SettingsSubTabBar tabs={TABS} />
      <Outlet />
    </>
  );
}
