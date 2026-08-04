// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useMutation } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';
import type { FieldId } from '@/types/field-id';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type QueryType = 'distribution' | 'metric' | 'trend' | 'comparison' | 'correlation' | 'history_trend';

export interface ChartJoin {
  table: string;
  on: string;
  type?: 'inner' | 'left' | 'right';
}

export interface ChartFilter {
  field_id: FieldId;
  operator: string;
  value?: any;
}

export interface ChartQueryConfig {
  queryType: QueryType;
  from: {
    table: string;
    joins?: ChartJoin[];
  };
  select: {
    dimension?: string;
    metric?: string;
    aggregation?: string;
    [key: string]: any;
  };
  filters?: ChartFilter[];
  options?: {
    limit?: number;
    orderBy?: string;
    orderDir?: 'asc' | 'desc';
    [key: string]: any;
  };
}

export interface ChartMetadata {
  total: number;
  executionTime: number;
  cached: boolean;
  timestamp: string;
  sql?: string;
}

export interface ChartQueryResponse {
  success: boolean;
  queryType: QueryType;
  data: Record<string, any>[];
  metadata: ChartMetadata;
}

export interface ChartQueryError {
  success: false;
  error: {
    code: string;
    message: string;
    details?: Record<string, any>;
  };
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const chartsApi = {
  query: (config: ChartQueryConfig) =>
    apiClient.post<ChartQueryResponse>('/charts/query', config),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useChartQuery(
  options?: Omit<UseMutationOptions<ChartQueryResponse, Error, ChartQueryConfig>, 'mutationFn'>,
) {
  return useMutation<ChartQueryResponse, Error, ChartQueryConfig>({
    mutationFn: (config) => chartsApi.query(config),
    ...options,
  });
}
