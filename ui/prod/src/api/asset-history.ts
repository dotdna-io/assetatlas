// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface HistoryAsset {
  id: string;
  hostname: string | null;
  ip: string | null;
  mac: string | null;
  category: string | null;
  os: string | null;
  last_seen: string | null;
}

export interface HistorySummary {
  total_events: number;
  attribute_changes: number;
  alerts: number;
}

export interface HistoryEvent {
  id: string;
  timestamp: string;
  type: 'attribute' | 'alert';
  // Attribute events
  action?: string;
  attribute?: string;
  old_value?: string | null;
  new_value?: string | null;
  source?: string | null;
  // Alert events
  alert_id?: string;
  alert_type?: string;
  severity?: string;
  title?: string;
  description?: string;
}

export interface AssetHistoryResponse {
  asset: HistoryAsset;
  summary: HistorySummary;
  events: HistoryEvent[];
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useAssetHistory(assetId: string | null, timeRange: string) {
  return useApiQuery<AssetHistoryResponse>(
    ['asset-history', assetId, timeRange],
    `/assets/${assetId}/history`,
    {
      enabled: !!assetId,
      params: { time_range: timeRange, limit: 500 },
      staleTime: 30_000,
      retry: 1,
    },
  );
}
