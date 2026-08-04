// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DashboardSummary {
  totalAssets: number;
  categoryBreakdown: Record<string, number> | { category: string; count: number }[];
  changeToday: { added: number; removed: number };
  dataQuality: { rating: string; confidenceDistribution: { high: number; medium: number; low: number } };
  visibility: { score: number; level: string };
  reliability: Record<string, any>;
  assetsByConfidence?: { highMedium: number; low: number };
  sourceCoverage?: { category: string; source_count: number }[];
  completeness?: number;
  lastUpdated?: string | null;
}

export interface DashboardInventory {
  totalAssets: number;
  lastUpdated: string | null;
  changeToday: { added: number; removed: number };
  assetTrend: number[] | null;
  categoryGauges: Record<string, any>[];
}

export interface DataSourceInfo {
  name: string;
  logo: string | null;
  asset_count: number;
  last_sync: string | null;
  status: string;
  [key: string]: any;
}

export interface DataSourcesResponse {
  count: number;
  sources: DataSourceInfo[];
}

export interface ReliabilityResponse {
  mostReliableSource: Record<string, any> | null;
  leastReliableSource: Record<string, any> | null;
  mostReliableCategory: Record<string, any> | null;
  leastReliableCategory: Record<string, any> | null;
}

export interface DashboardAlertsResponse {
  count: number;
  summary: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  alerts: Record<string, any>[];
}

export interface WheelsDataResponse {
  totalAssets: number;
  categories: Record<string, any>[];
  subcategories: Record<string, any>[];
  operatingSystems: Record<string, any>[];
  dataSources: Record<string, any>[];
}

export interface PostureTierCounts {
  secured: number;
  monitored: number;
  discovered: number;
}

export interface PostureChange {
  delta: number | null;
  period: 'week';
}

export interface DashboardPostureResponse {
  posture: Record<string, PostureTierCounts>;
  changes: {
    secured: PostureChange;
    monitored: PostureChange;
    discovered: PostureChange;
  };
}

export interface AssetTimelineResponse {
  range: string;
  granularity: string;
  data: { label: string; added: number; returned: number; removed: number }[];
}

export interface CoverageTrendResponse {
  metric: string;
  range: string;
  granularity: string;
  data: { label: string; added: number; removed: number }[];
}

export interface ConfidenceBreakdownResponse {
  breakdown: Record<string, { confirmed: number; under_covered: number; shadows: number }>;
}

export interface PostureTrendResponse {
  days: number;
  series: {
    secured: number[];
    monitored: number[];
    discovered: number[];
  };
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const dashboardApi = {
  getSummary: () =>
    apiClient.get<DashboardSummary>('/dashboard/summary'),

  getInventory: () =>
    apiClient.get<DashboardInventory>('/dashboard/inventory'),

  getDataSources: (view?: 'individual' | 'grouped') =>
    apiClient.get<DataSourcesResponse>('/dashboard/data-sources', { params: view ? { view } : undefined }),

  getReliability: () =>
    apiClient.get<ReliabilityResponse>('/dashboard/reliability'),

  getAlerts: () =>
    apiClient.get<DashboardAlertsResponse>('/dashboard/alerts'),

  getWheelsData: () =>
    apiClient.get<WheelsDataResponse>('/dashboard/wheels-data'),

  getPosture: () =>
    apiClient.get<DashboardPostureResponse>('/dashboard/posture'),

  getAssetTimeline: (days: number) =>
    apiClient.get<AssetTimelineResponse>('/dashboard/asset-timeline', { params: { days } }),

  getCoverageTrend: (metric: string, days: number) =>
    apiClient.get<CoverageTrendResponse>('/dashboard/coverage-trend', { params: { metric, days } }),

  getPostureTrend: (days: number) =>
    apiClient.get<PostureTrendResponse>('/dashboard/posture-trend', { params: { days } }),

  getConfidenceBreakdown: () =>
    apiClient.get<ConfidenceBreakdownResponse>('/dashboard/confidence-breakdown'),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

const DASHBOARD_STALE = 60 * 1000; // 1 minute (matches server cache)

export function useDashboardSummary() {
  return useApiQuery<DashboardSummary>(
    ['dashboard', 'summary'],
    '/dashboard/summary',
    { staleTime: DASHBOARD_STALE },
  );
}

export function useDashboardInventory() {
  return useApiQuery<DashboardInventory>(
    ['dashboard', 'inventory'],
    '/dashboard/inventory',
    { staleTime: DASHBOARD_STALE },
  );
}

export function useDataSources(view?: 'individual' | 'grouped') {
  return useApiQuery<DataSourcesResponse>(
    ['dashboard', 'data-sources', view],
    '/dashboard/data-sources',
    { params: view ? { view } : undefined, staleTime: DASHBOARD_STALE },
  );
}

export function useDashboardReliability() {
  return useApiQuery<ReliabilityResponse>(
    ['dashboard', 'reliability'],
    '/dashboard/reliability',
    { staleTime: DASHBOARD_STALE },
  );
}

export function useDashboardAlerts() {
  return useApiQuery<DashboardAlertsResponse>(
    ['dashboard', 'alerts'],
    '/dashboard/alerts',
    { staleTime: DASHBOARD_STALE },
  );
}

export function useWheelsData() {
  return useApiQuery<WheelsDataResponse>(
    ['dashboard', 'wheels-data'],
    '/dashboard/wheels-data',
    { staleTime: DASHBOARD_STALE },
  );
}

export function useDashboardPosture() {
  return useApiQuery<DashboardPostureResponse>(
    ['dashboard', 'posture'],
    '/dashboard/posture',
    { staleTime: DASHBOARD_STALE },
  );
}

export function useDashboardAssetTimeline(days: number) {
  return useApiQuery<AssetTimelineResponse>(
    ['dashboard', 'asset-timeline', days],
    '/dashboard/asset-timeline',
    { params: { days }, staleTime: DASHBOARD_STALE },
  );
}

export function useDashboardCoverageTrend(metric: string, days: number, enabled = true) {
  return useApiQuery<CoverageTrendResponse>(
    ['dashboard', 'coverage-trend', metric, days],
    '/dashboard/coverage-trend',
    { params: { metric, days }, staleTime: DASHBOARD_STALE, enabled },
  );
}

export function useDashboardConfidence() {
  return useApiQuery<ConfidenceBreakdownResponse>(
    ['dashboard', 'confidence-breakdown'],
    '/dashboard/confidence-breakdown',
    { staleTime: DASHBOARD_STALE },
  );
}

export function useDashboardPostureTrend(days: number) {
  return useApiQuery<PostureTrendResponse>(
    ['dashboard', 'posture-trend', days],
    '/dashboard/posture-trend',
    { params: { days }, staleTime: DASHBOARD_STALE },
  );
}
