// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const usePipelineRunDetailMock = vi.fn();
const usePipelineRunPhasesMock = vi.fn();

vi.mock('@/api/pipeline', () => ({
  usePipelineRunDetail: (...args: unknown[]) => usePipelineRunDetailMock(...args),
  usePipelineRunPhases: (...args: unknown[]) => usePipelineRunPhasesMock(...args),
}));

vi.mock('@/components/ui/LoadingSpinner', () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));

import { RunDetailPanel } from '../RunDetailPanel';

beforeEach(() => {
  usePipelineRunDetailMock.mockReset();
  usePipelineRunPhasesMock.mockReset();
});

function setRun(stats: Record<string, unknown> = {}, overrides: Record<string, unknown> = {}) {
  usePipelineRunDetailMock.mockReturnValue({
    data: {
      id: 'run-1',
      status: 'completed',
      triggered_by: 'manual',
      started_at: '2026-05-04T10:00:00Z',
      completed_at: '2026-05-04T10:01:00Z',
      duration_seconds: 60,
      assets_created: 0,
      assets_readded: 0,
      assets_updated: 0,
      assets_removed: 0,
      batches_processed: 0,
      total_assets: 0,
      error_message: null,
      stats,
      ...overrides,
    },
    isLoading: false,
    isError: false,
  });
}

function setPhases(phases: Array<Record<string, unknown>>) {
  usePipelineRunPhasesMock.mockReturnValue({
    data: { run_id: 'run-1', status: 'completed', phases },
    isLoading: false,
  });
}

function phase(over: Record<string, unknown> = {}) {
  return {
    key: 'enrich_score',
    label: 'Enrich & Score',
    duration_seconds: 1.0,
    status: 'success',
    sub_stages: [],
    has_slow_stages: false,
    ...over,
  };
}

describe('RunDetailPanel — Stage timings as expandable phase rows', () => {
  it('renders a row per phase from the phases endpoint', () => {
    setRun();
    setPhases([
      phase({ key: 'ingest_identify',     label: 'Ingest & Identify',     duration_seconds: 1.0 }),
      phase({ key: 'enrich_score',        label: 'Enrich & Score',        duration_seconds: 2.0 }),
      phase({ key: 'persist_apply_rules', label: 'Persist & Apply Rules', duration_seconds: 3.0 }),
      phase({ key: 'post_process',        label: 'Post-process',          duration_seconds: 4.0 }),
    ]);

    render(<RunDetailPanel runId="run-1" />);

    expect(screen.getByText('Ingest & Identify')).toBeInTheDocument();
    expect(screen.getByText('Enrich & Score')).toBeInTheDocument();
    expect(screen.getByText('Persist & Apply Rules')).toBeInTheDocument();
    expect(screen.getByText('Post-process')).toBeInTheDocument();
  });

  it('does not render legacy per-stage rows from stats.stage_timings', () => {
    setRun({
      stage_timings: [
        { name: 'preprocessing', duration: 0.5 },
        { name: 'scoring', duration: 1.2 },
      ],
    });
    setPhases([phase({ duration_seconds: 1.2 })]);

    render(<RunDetailPanel runId="run-1" />);

    // Stats's `stage_timings` is no longer rendered; the bar list (collapsed
    // by default with no slow stages) is sourced from sub_stages instead.
    expect(screen.queryByText('preprocessing')).not.toBeInTheDocument();
  });

  it('phase row is collapsed by default when there are no slow stages', () => {
    setRun();
    setPhases([
      phase({
        sub_stages: [
          { stage_key: 'scoring', duration_seconds: 1.0, p95_seconds: 1.0,
            samples: 30, is_slow: false },
        ],
        has_slow_stages: false,
      }),
    ]);

    render(<RunDetailPanel runId="run-1" />);

    const details = screen.getByText('Enrich & Score').closest('details');
    expect(details).toBeInTheDocument();
    expect(details).not.toHaveAttribute('open');
  });

  it('phase row auto-expands when has_slow_stages is true', () => {
    setRun();
    setPhases([
      phase({
        sub_stages: [
          { stage_key: 'scoring', duration_seconds: 5.0, p95_seconds: 1.0,
            samples: 30, is_slow: true },
        ],
        has_slow_stages: true,
      }),
    ]);

    render(<RunDetailPanel runId="run-1" />);

    const details = screen.getByText('Enrich & Score').closest('details');
    expect(details).toHaveAttribute('open');
  });

  it('renders bar rows from sub_stages and tags slow ones', () => {
    setRun();
    setPhases([
      phase({
        has_slow_stages: true,
        sub_stages: [
          { stage_key: 'preprocessing', duration_seconds: 0.5, p95_seconds: 0.4,
            samples: 30, is_slow: false },
          { stage_key: 'scoring', duration_seconds: 5.0, p95_seconds: 1.0,
            samples: 30, is_slow: true },
        ],
      }),
    ]);

    render(<RunDetailPanel runId="run-1" />);

    // Stage names are humanized (underscores → spaces).
    expect(screen.getByText('preprocessing')).toBeInTheDocument();
    expect(screen.getByText('scoring')).toBeInTheDocument();
    // The slow stage gets a "slow" badge.
    expect(screen.getByText('slow')).toBeInTheDocument();
  });

  it('shows phase-level "slow stages" indicator when any sub stage is slow', () => {
    setRun();
    setPhases([
      phase({
        has_slow_stages: true,
        sub_stages: [
          { stage_key: 'scoring', duration_seconds: 5.0, p95_seconds: 1.0,
            samples: 30, is_slow: true },
        ],
      }),
    ]);

    render(<RunDetailPanel runId="run-1" />);
    expect(screen.getByText(/slow stages/i)).toBeInTheDocument();
  });

  it('renders the phases section heading "Stage timings"', () => {
    setRun();
    setPhases([phase()]);
    render(<RunDetailPanel runId="run-1" />);
    expect(screen.getByText('Stage timings')).toBeInTheDocument();
  });

  it('renders a "Returned" metric card', () => {
    setRun();
    setPhases([phase()]);
    render(<RunDetailPanel runId="run-1" />);
    expect(screen.getByText('Returned')).toBeInTheDocument();
  });

  it('renders a "Merged" metric card with the merged_assets value', () => {
    setRun({ merged_assets: 9 });
    setPhases([phase()]);
    render(<RunDetailPanel runId="run-1" />);
    expect(screen.getByText('Merged')).toBeInTheDocument();
    expect(screen.getByText('9')).toBeInTheDocument();
  });
});

describe('RunDetailPanel — grace carry-forward reconciliation', () => {
  // The aggregation stage counts unique_assets BEFORE the grace stage (s15c)
  // re-injects absent-but-in-grace assets, so the funnel's "Unique Assets"
  // terminal (169) can't be reconciled with the run-history "Total" (186) or
  // Explorer's inventory count. Surfacing the carried count closes that gap:
  // unique (169) + carried (17) = total (186). Uses stats.total_assets (not the
  // top-level data.total_assets, which the run-detail endpoint doesn't return).
  it('renders Carried Forward and Total cards that reconcile unique + carried = total', () => {
    setRun({ unique_assets: 169, grace_carried_total: 17, total_assets: 186 });
    setPhases([phase()]);
    render(<RunDetailPanel runId="run-1" />);

    expect(screen.getByText('169')).toBeInTheDocument();
    expect(screen.getByText('17')).toBeInTheDocument();
    expect(screen.getByText('186')).toBeInTheDocument();

    // The reconciliation only reads if the chain flows Unique → Carried → Total.
    const uniqueLabel = screen.getByText('Unique Assets');
    const carriedLabel = screen.getByText('Carried Forward');
    const totalLabel = screen.getByText('Total');
    const follows = (a: Node, b: Node) =>
      // eslint-disable-next-line no-bitwise
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(follows(uniqueLabel, carriedLabel)).toBe(true);
    expect(follows(carriedLabel, totalLabel)).toBe(true);
  });

  // A clean run (grace carried nothing) must stay uncluttered: Unique Assets IS
  // the total, so the extra cards would be redundant noise.
  it('omits Carried Forward and Total cards when nothing was carried forward', () => {
    setRun({ unique_assets: 169, grace_carried_total: 0, total_assets: 169 });
    setPhases([phase()]);
    render(<RunDetailPanel runId="run-1" />);

    expect(screen.getByText('Unique Assets')).toBeInTheDocument();
    expect(screen.queryByText('Carried Forward')).not.toBeInTheDocument();
    expect(screen.queryByText('Total')).not.toBeInTheDocument();
  });
});
