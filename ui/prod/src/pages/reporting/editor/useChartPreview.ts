// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useCallback, useEffect } from 'react';
import { chartsApi } from '@/api/charts';
import {
  transformToAPIRequest,
  transformAPIDataForChart,
  hasRenderableQuery,
} from '../data/chartDataPipeline';

interface PreviewState {
  data: any;
  loading: boolean;
  error: string | null;
}

/**
 * Debounced API query for live chart preview.
 *
 * Transforms the current queryConfig into an API request, fetches data,
 * and transforms the response for the target chart class. Skips queries
 * when the config lacks both a dimension and a metric.
 */
export function useChartPreview(
  queryConfig: Record<string, any> | null,
  chart: { className: string },
  debounceMs = 500,
): PreviewState {
  const [state, setState] = useState<PreviewState>({
    data: undefined,
    loading: false,
    error: null,
  });

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef(false);

  const fetchPreview = useCallback(
    (config: Record<string, any>) => {
      abortRef.current = false;
      setState((prev) => ({ ...prev, loading: true, error: null }));

      const apiRequest = transformToAPIRequest(config);

      chartsApi
        .query(apiRequest)
        .then((response) => {
          if (abortRef.current) return;
          // apiClient unwraps { success, data } — response IS the data array
          const transformed = transformAPIDataForChart(response as any, {
            className: chart.className,
            queryConfig: config,
          });
          setState({ data: transformed, loading: false, error: null });
        })
        .catch((err) => {
          if (abortRef.current) return;
          setState({
            data: undefined,
            loading: false,
            error: err?.message || 'Preview failed',
          });
        });
    },
    [chart.className],
  );

  useEffect(() => {
    // Clear previous timer
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    // Reset preview state when there's no active config (e.g., modal closed
    // and ChartEditor is rendering with chart=null). Without this, stale
    // `data` from a previous open survives across modal close, and on the
    // next open the chart constructor renders that stale data — visible
    // as an extra animation before the new fetch resolves.
    if (!queryConfig) {
      setState((prev) =>
        prev.data === undefined && !prev.loading && !prev.error
          ? prev
          : { data: undefined, loading: false, error: null },
      );
      return;
    }
    if (!hasRenderableQuery(queryConfig)) return;

    timerRef.current = setTimeout(() => {
      fetchPreview(queryConfig);
    }, debounceMs);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [queryConfig, debounceMs, fetchPreview]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current = true;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  return state;
}
