// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AssetTag {
  name: string;
  color: string;
  type: string; // 'pass' | 'fail' | 'classification'
}

export interface AssetStatus {
  is_compliant: boolean | null;
  is_secure: boolean | null;
  alert_count: number;
  in_grace: boolean;
  days_in_grace: number | null;
}

export interface ConfidenceBreakdown {
  source_count: number;
  available_sources: number;
  source_count_score: number;
  source_reliability: number;
  data_completeness: number;
  missing_fields: string[];
  consistency_penalty: number;
  conflicting_fields: string[];
  recency_penalty: number;
  identifier_type: string;
  identifier_cap: number;
  [key: string]: unknown;
}

export interface AssetDetail {
  id: string;
  hostname: string | null;
  hostnames: string[];
  fqdn: string | null;
  category: string | null;
  subcategory: string | null;
  vendor_category: string | null;
  os: string | null;
  model: string | null;
  manufacturer: string | null;
  ip: string[];
  mac: string[];
  mac_vendor: string | null;
  sn: string | null;
  confidence: number;
  confidence_level: string;
  confidence_explanation: string;
  confidence_breakdown: ConfidenceBreakdown | null;
  last_seen: string | null;
  first_seen: string | null;
  tags: AssetTag[];
  status: AssetStatus;
  owner: string | null;
  owners: string[];
  location: string | null;
  locations: string[];
  ipam_location: string | null;
  ipam_locations: string[];
  user: string | null;
  users: string[];
  extended_attributes: Record<string, Record<string, unknown>>;
}

export interface ProviderFields {
  general: Record<string, unknown>;
  network: Record<string, unknown>;
  additional: Record<string, unknown>;
  extended: Record<string, unknown>;
}

export interface AssetProvider {
  source_id: string;
  source_name: string | null;
  source_icon: string;
  source_type: string;
  source_category: string | null;
  status: string;
  last_sync: string | null;
  field_count: number;
  record_count: number;
  fields: ProviderFields;
}

export interface OtherSource {
  source_name: string;
  source_icon: string;
  source_type: string;
  asset_count: number;
}

export interface AssetDetailResponse {
  asset: AssetDetail;
  providers: AssetProvider[];
  other_sources: OtherSource[];
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export const assetDetailsApi = {
  getAssetDetail: (assetId: string) =>
    apiClient.get<AssetDetailResponse>(`/assets/${assetId}`),
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useAssetDetail(assetId: string | null) {
  return useApiQuery<AssetDetailResponse>(
    ['asset-detail', assetId],
    `/assets/${assetId}`,
    {
      enabled: !!assetId,
      staleTime: 60_000,
      retry: 1,
    },
  );
}
