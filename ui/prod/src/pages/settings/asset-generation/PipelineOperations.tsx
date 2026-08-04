// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  usePipelineStatus,
  usePipelineRuns,
  useTriggerPipeline,
} from '@/api/pipeline';
import type { PipelineRun } from '@/api/pipeline';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { RunDetailPanel } from './RunDetailPanel';
import styles from './PipelineOperations.module.css';

const RUNS_PER_PAGE = 10;

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '--';
  seconds = Math.round(seconds);
  if (seconds < 60) return seconds + 's';
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return secs > 0 ? mins + 'm ' + secs + 's' : mins + 'm';
}

function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '--';
  const d = new Date(isoString);
  const Y = d.getFullYear();
  const M = String(d.getMonth() + 1).padStart(2, '0');
  const D = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  return Y + '-' + M + '-' + D + ' ' + h + ':' + m;
}

function isActiveStatus(s: string | undefined): boolean {
  return s === 'running' || s === 'pending';
}

export function PipelineOperations() {
  const qc = useQueryClient();
  const [statusMsg, setStatusMsg] = useInlineStatus();
  const { data: status } = usePipelineStatus();
  const [page, setPage] = useState(1);
  const { data: runsData } = usePipelineRuns({ page, per_page: RUNS_PER_PAGE });
  const triggerMutation = useTriggerPipeline();

  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number>(0);
  const prevStatusRef = useRef<string | undefined>(undefined);
  const startTimeRef = useRef<number | null>(null);

  const isRunning = isActiveStatus(status?.status);

  // Sync elapsed time from server, then tick locally
  useEffect(() => {
    if (isRunning && status?.elapsed_seconds != null) {
      startTimeRef.current = Date.now() - status.elapsed_seconds * 1000;
      setElapsed(status.elapsed_seconds);
    } else {
      startTimeRef.current = null;
    }
  }, [isRunning, status?.elapsed_seconds]);

  useEffect(() => {
    if (!isRunning || startTimeRef.current == null) return;
    const timer = setInterval(() => {
      if (startTimeRef.current != null) {
        setElapsed(Math.round((Date.now() - startTimeRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [isRunning]);

  // On status transition: running -> complete/failed, refresh runs
  useEffect(() => {
    const prevActive = isActiveStatus(prevStatusRef.current);
    const nowActive = isActiveStatus(status?.status);
    if (prevActive && !nowActive) {
      qc.invalidateQueries({ queryKey: ['pipeline', 'runs'] });
    }
    prevStatusRef.current = status?.status;
  }, [status?.status, qc]);

  const handleTrigger = useCallback(() => {
    // No success feedback: a trigger only queues the run (it can still fail),
    // and the live "Running…" status panel is the real confirmation.
    triggerMutation.mutate(undefined, {
      onError: (err) => setStatusMsg({ kind: 'error', message: err.message || 'Failed to trigger pipeline' }),
    });
  }, [triggerMutation, setStatusMsg]);

  const toggleExpanded = useCallback((runId: string) => {
    setExpandedRunId((prev) => (prev === runId ? null : runId));
  }, []);

  const runs = runsData?.runs ?? [];
  const totalPages = runsData?.pages ?? 1;
  const totalEntries = runsData?.total ?? 0;

  return (
    <div className={styles.card}>
      {/* Padded so the message sits a little below the card's top edge
          (the card itself has no top padding). Only rendered when present
          so it reserves no space otherwise. */}
      {statusMsg && (
        <div className={styles.statusBar}>
          <InlineStatus status={statusMsg} />
        </div>
      )}
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <h3>Pipeline</h3>
          <p>Trigger runs, configure schedule, and review history</p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={handleTrigger}
          disabled={isRunning}
          loading={triggerMutation.isPending}
        >
          Run Now
        </Button>
      </div>

      {/* Live status panel */}
      {isRunning && (
        <div className={styles.statusPanel}>
          <div className={styles.pulseDot} />
          <span className={styles.statusLabel}>Running...</span>
          <div className={styles.sep} />
          <span className={styles.statusDetail}>
            Stage: <strong>{status?.current_stage || 'Initializing...'}</strong>
          </span>
          <div className={styles.sep} />
          <span className={styles.statusDetail}>
            Elapsed: <strong>{formatDuration(elapsed)}</strong>
          </span>
        </div>
      )}

      {/* Run History */}
      <div className={styles.historyTitle}>Run History</div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Triggered By</th>
              <th>Started</th>
              <th>Duration</th>
              <th>Status</th>
              <th className={styles.textRight}>Total</th>
              <th className={styles.textRight}>Created</th>
              <th className={styles.textRight}>Returned</th>
              <th className={styles.textRight}>Updated</th>
              <th className={styles.textRight}>Removed</th>
              <th className={styles.textRight}>Merged</th>
            </tr>
          </thead>
          {runs.length === 0 ? (
            <tbody>
              <tr>
                <td colSpan={10} className={styles.empty}>No pipeline runs yet</td>
              </tr>
            </tbody>
          ) : (
            runs.map((run: PipelineRun) => {
              const isExpanded = expandedRunId === run.id;
              const statusClass = styles['status' + run.status.charAt(0).toUpperCase() + run.status.slice(1)] || '';

              return (
                <tbody key={run.id}>
                  <tr className={styles.runRow} onClick={() => toggleExpanded(run.id)}>
                    <td>
                      <span className={isExpanded ? styles.chevronExpanded : styles.chevron}>
                        {'\u25B6'}
                      </span>
                      <span className={run.triggered_by === 'schedule' ? styles.badgeSchedule : styles.badgeManual}>
                        {run.triggered_by === 'schedule' ? 'schedule' : 'manual'}
                      </span>
                    </td>
                    <td>{formatDateTime(run.started_at)}</td>
                    <td>{formatDuration(run.duration_seconds)}</td>
                    <td>
                      <span className={[styles.statusBadge, statusClass].filter(Boolean).join(' ')}>
                        {run.status}
                      </span>
                    </td>
                    <td className={styles.textRight}>
                      {run.total_assets != null ? run.total_assets.toLocaleString() : '--'}
                    </td>
                    <td className={styles.textRight}>
                      <span className={run.assets_created > 0 ? styles.countGreen : ''}>
                        {run.assets_created != null ? run.assets_created.toLocaleString() : '--'}
                      </span>
                    </td>
                    <td className={styles.textRight}>
                      <span className={(run.assets_readded ?? 0) > 0 ? styles.countOrange : ''}>
                        {run.assets_readded != null ? run.assets_readded.toLocaleString() : '--'}
                      </span>
                    </td>
                    <td className={styles.textRight}>
                      <span className={run.assets_updated > 0 ? styles.countBlue : ''}>
                        {run.assets_updated != null ? run.assets_updated.toLocaleString() : '--'}
                      </span>
                    </td>
                    <td className={styles.textRight}>
                      <span className={run.assets_removed > 0 ? styles.countRed : ''}>
                        {run.assets_removed != null ? run.assets_removed.toLocaleString() : '--'}
                      </span>
                    </td>
                    <td className={styles.textRight}>
                      <span className={(run.assets_merged ?? 0) > 0 ? styles.countMerged : ''}>
                        {run.assets_merged != null ? run.assets_merged.toLocaleString() : '--'}
                      </span>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className={styles.detailRowVisible}>
                      <td colSpan={10}>
                        <RunDetailPanel runId={run.id} />
                      </td>
                    </tr>
                  )}
                </tbody>
              );
            })
          )}
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className={styles.pagination}>
          <button
            className={styles.paginationBtn}
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <span className={styles.pageInfo}>
            Page {page} of {totalPages} ({totalEntries} entries)
          </span>
          <button
            className={styles.paginationBtn}
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
