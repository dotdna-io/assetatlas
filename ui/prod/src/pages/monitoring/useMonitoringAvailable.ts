// SPDX-License-Identifier: AGPL-3.0-or-later
import { useHealth } from '@/api/settings';

/**
 * Whether the Prometheus monitoring backend is available.
 *
 * Reads `application.features.monitoring` from the shared `useHealth()` query
 * (already polled on a 30s interval by the sidebar version probe, so this adds
 * no extra request). The `/api/monitoring/*` proxy blueprint is registered only
 * when `MONITORING_ENABLED=true`; otherwise every query 404s. This flag mirrors
 * that server state so the UI can hide the dashboards instead of firing a wall
 * of doomed requests.
 *
 * Returns false while health is still loading — the dashboards fire many
 * requests on mount, so we only render them once monitoring is confirmed on.
 */
export function useMonitoringAvailable(): boolean {
  const { data } = useHealth();
  return data?.application?.features?.monitoring === true;
}
