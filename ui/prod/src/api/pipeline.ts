// SPDX-License-Identifier: AGPL-3.0-or-later
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationOptions } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Types (matched to actual API responses)
// ---------------------------------------------------------------------------

export interface PipelineLastRun {
  run_id: string;
  status: string;
  duration_seconds: number | null;
  assets_created: number;
  assets_readded: number;
  assets_updated: number;
  completed_at: string | null;
  triggered_by: string;
}

export interface PipelineStatus {
  status: 'idle' | 'running';
  last_run: PipelineLastRun | null;
  // Running-only fields (may be present when status === 'running')
  current_stage?: string;
  progress?: number;
  elapsed_seconds?: number;
  next_run_at?: string;
}

export interface PipelineRun {
  id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  triggered_by: string;
  started_at: string;
  completed_at: string | null;
  created_at: string | null;
  duration_seconds: number | null;
  assets_created: number;
  assets_readded: number;
  assets_updated: number;
  assets_removed: number;
  assets_merged: number;
  batches_processed: number;
  total_assets: number;
  error_message: string | null;
}

export interface PipelineRunsResponse {
  runs: PipelineRun[];
  total: number;
  page: number;
  pages: number;
  per_page: number;
}

// ---------------------------------------------------------------------------
// Raw API functions
// ---------------------------------------------------------------------------

export const pipelineApi = {
  getStatus: () =>
    apiClient.get<PipelineStatus>('/pipeline/status'),

  trigger: () =>
    apiClient.post<any>('/pipeline/trigger'),

  getRuns: (params?: { page?: number; per_page?: number }) =>
    apiClient.get<PipelineRunsResponse>('/pipeline/runs', { params: params as Record<string, any> }),
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function usePipelineStatus() {
  return useApiQuery<PipelineStatus>(
    ['pipeline', 'status'],
    '/pipeline/status',
    {
      refetchInterval: (query) =>
        (query.state.data as PipelineStatus)?.status === 'running' ? 3000 : 15000,
    },
  );
}

/** Answer of GET /pipeline/data-version: the last run to reach a terminal state. */
export interface PipelineDataVersion {
  run_id: string | null;
  completed_at: string | null;
  status: 'completed' | 'failed' | null;
}

/**
 * Identifies the data currently in the asset table. Its `run_id` changes every
 * time a pipeline run finishes, which is the only signal the UI gets that a run
 * rebuilt `assets` from scratch and left every cached query describing rows
 * that no longer exist.
 *
 * Readable by any authenticated role (the endpoint is deliberately not
 * admin-gated, unlike the rest of /pipeline), because it is mounted app-wide —
 * see DataVersionWatcher in AppShell. One request per minute per open tab is
 * the intended cost; TanStack's default refetchOnWindowFocus adds an immediate
 * check when the operator comes back to the tab, which is when it matters most.
 */
export function useDataVersion() {
  return useApiQuery<PipelineDataVersion>(
    ['pipeline', 'data-version'],
    '/pipeline/data-version',
    { refetchInterval: 60_000 },
  );
}

export function usePipelineRuns(params?: { page?: number; per_page?: number }) {
  return useApiQuery<PipelineRunsResponse>(
    ['pipeline', 'runs', JSON.stringify(params)],
    '/pipeline/runs',
    { params: params as Record<string, any> },
  );
}

export interface PipelineRunDetail extends PipelineRun {
  stats?: {
    batch_results?: Array<{
      integration_name?: string;
      integration_type?: string;
      batch_id?: string;
      input_records: number;
      unique_assets?: number;
      excluded?: number;
      success?: boolean;
      error?: string;
    }>;
    stage_timings?: Array<{
      name: string;
      duration: number;
    }>;
    chronicle_events?: Record<string, number>;
    input_records?: number;
    excluded_records?: number;
    expanded_records?: number;
    unique_assets?: number;
    // Assets persisted after the grace stage (s15c) re-injects carried-forward
    // assets: total_assets === unique_assets + grace_carried_total. This is the
    // figure the run-history "Total" column and Explorer's inventory count show;
    // the run-detail endpoint returns it only inside stats (not at top level).
    total_assets?: number;
    // Absent-but-in-grace assets carried forward this run (#795); 0 on clean runs.
    grace_carried_total?: number;
    total_assets_created?: number;
    total_assets_updated?: number;
    removed_assets?: number;
    merged_assets?: number;
  };
}

export function usePipelineRunDetail(runId: string | null) {
  return useApiQuery<PipelineRunDetail>(
    ['pipeline', 'runs', runId],
    '/pipeline/runs/' + runId,
    { enabled: !!runId, staleTime: Infinity },
  );
}

// ---------------------------------------------------------------------------
// 4-phase operator view
// ---------------------------------------------------------------------------

export interface PipelineSubStage {
  stage_key: string;
  duration_seconds: number;
  p95_seconds: number | null;
  samples: number;
  is_slow: boolean;
}

export interface PipelinePhase {
  key: 'ingest_identify' | 'enrich_score' | 'persist_apply_rules' | 'post_process';
  label: string;
  duration_seconds: number;
  status: 'success' | 'failed' | 'not_run' | 'running' | 'unknown';
  sub_stages: PipelineSubStage[];
  has_slow_stages: boolean;
}

export interface PipelineRunPhases {
  run_id: string;
  status: string;
  phases: PipelinePhase[];
}

export function usePipelineRunPhases(runId: string | null) {
  return useApiQuery<PipelineRunPhases>(
    ['pipeline', 'runs', runId, 'phases'],
    '/pipeline/runs/' + runId + '/phases',
    { enabled: !!runId, staleTime: Infinity },
  );
}

export function useTriggerPipeline(
  options?: Omit<UseMutationOptions<any, Error, void>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...restOptions } = options || {};
  return useMutation<any, Error, void>({
    mutationFn: () => pipelineApi.trigger(),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['pipeline'] });
      onSuccess?.(...args);
    },
    ...restOptions,
  });
}
