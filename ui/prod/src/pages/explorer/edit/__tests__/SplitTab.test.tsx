// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { SplitTab } from '../SplitTab';
import type { SplitPreview } from '@/api/asset-split';

// Mock the auth + data hooks only — same approach as MergeDenylistTab.test.tsx /
// MergeKeys.test.tsx — so the tab renders without a live backend and
// `mutate` is a spy we can assert on instead of a real network call.
vi.mock('@/auth/useAuth', () => ({ useAuth: () => ({ isAdmin: true }) }));

const preview = vi.fn();
const mutate = vi.fn();
vi.mock('@/api/asset-split', () => ({
  useSplitPreview: (...args: unknown[]) => preview(...args),
  useExecuteSplit: () => ({ mutate, isPending: false }),
}));

function renderTab(assetId = 'a1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <SplitTab assetId={assetId} onSuccess={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const MERGE_KEYS = [
  { type: 'hostname', value: 'web01', component: 'web01' },
  { type: 'mac', value: 'aa:bb:cc:dd:ee:01', component: 'aa:bb:cc:dd:ee:01' },
  // component carries the 'sn:' prefix the picker must send back as `bridge`;
  // value is the bare display form — the two diverge only for serials.
  { type: 'serial', value: 'abc123', component: 'sn:abc123' },
];

const SPLIT_FOUND: SplitPreview = {
  merge_keys: MERGE_KEYS,
  bridge: 'web01',
  would_split: true,
  warnings: [],
  next_run_at: null,
  clusters: [
    ['bsc_camera_side1', 'ec:71:db:7e:e1:3e'],
    ['bsc_camera_side2', 'ec:71:db:cb:20:0b'],
  ],
};

const SPLIT_WEAK: SplitPreview = {
  merge_keys: MERGE_KEYS,
  bridge: 'ec:71:db:7e:e1:3e',
  would_split: true,
  warnings: ["Device with frozenset({'bsc_camera_side2'}) would be left without a unique identifier"],
  next_run_at: null,
  clusters: [
    ['bsc_camera_side1', 'ec:71:db:7e:e1:3e'],
    ['bsc_camera_side2'],
  ],
};

const NO_SPLIT: SplitPreview = {
  merge_keys: MERGE_KEYS,
  bridge: null,
  would_split: false,
  warnings: [],
  next_run_at: null,
  clusters: [],
};

/**
 * useSplitPreview is called twice per render — the auto-detect call (2nd arg
 * `bridge` undefined) and the manual-override call (2nd arg = the picked
 * identifier, only enabled once the admin picks one). Key the mock on that
 * 2nd arg so each call gets the right fixture, exactly like the real preview
 * endpoint would respond differently for an unscoped vs. bridge-scoped query.
 */
function mockPreview(autoData: SplitPreview, manualData?: (bridge: string) => SplitPreview) {
  preview.mockImplementation((_assetId: string | null, bridge?: string) => {
    if (bridge && manualData) return { data: manualData(bridge), isLoading: false };
    return { data: autoData, isLoading: false };
  });
}

describe('SplitTab', () => {
  beforeEach(() => {
    preview.mockReset();
    mutate.mockReset();
  });

  it('auto-detect: renders both proposed device clusters and a Split asset button', () => {
    mockPreview(SPLIT_FOUND);
    renderTab();

    expect(screen.getByText('bsc_camera_side1')).toBeInTheDocument();
    expect(screen.getByText('ec:71:db:7e:e1:3e')).toBeInTheDocument();
    expect(screen.getByText('bsc_camera_side2')).toBeInTheDocument();
    expect(screen.getByText('ec:71:db:cb:20:0b')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /split asset/i })).toBeInTheDocument();
  });

  it('blocks the Split button and shows a note when the preview has weak-cluster warnings (spec §7)', () => {
    // A manual cut that would orphan a device without a unique identifier
    // must be BLOCKED, not just warned about — auto-detect never produces
    // weak clusters, so this only bites the manual-override path.
    mockPreview(SPLIT_WEAK);
    renderTab();

    expect(screen.getByRole('button', { name: /split asset/i })).toBeDisabled();
    expect(screen.getByText(/can't split/i)).toBeInTheDocument();
  });

  it('no over-merge: shows a plain-language verdict and a Merge Denylist off-ramp, not a dead-end picker', () => {
    mockPreview(NO_SPLIT);
    renderTab();

    // A verdict up front, so the admin isn't left probing to discover the asset is fine.
    expect(screen.getByText(/no over-merge detected/i)).toBeInTheDocument();
    // Nothing is confirmable in this state.
    expect(screen.queryByRole('button', { name: /split asset/i })).not.toBeInTheDocument();
    // The real tool for a genuinely wrong merge, linked so it's actionable.
    const link = screen.getByRole('link', { name: /merge denylist/i });
    expect(link).toHaveAttribute('href', '/settings/data/merge-denylist');
    // The identifier probe is demoted behind an Advanced disclosure, not the primary UI.
    expect(screen.getByText(/advanced: test a specific identifier/i)).toBeInTheDocument();
  });

  it('Advanced probe: an identifier that would not split explains why, and stays usable', () => {
    // The picked bridge comes back would_split:false — a real "distrusting
    // this identifier wouldn't actually separate the devices" result.
    mockPreview(NO_SPLIT, (bridge) => ({ ...NO_SPLIT, bridge, would_split: false }));
    renderTab();

    fireEvent.click(screen.getByText(/advanced: test a specific identifier/i));
    fireEvent.click(screen.getByText('web01').closest('button')!);

    // Explains WHY it won't split, rather than a bare warning.
    expect(screen.getByText(/won.?t separate this asset/i)).toBeInTheDocument();
    // Not stuck: the other identifier is still available to try.
    expect(screen.getByText('aa:bb:cc:dd:ee:01')).toBeInTheDocument();
  });

  it('Advanced probe: a serial merge key sends its sn:-prefixed component as the bridge, not the bare value', () => {
    mockPreview(NO_SPLIT, (bridge) => ({ ...NO_SPLIT, bridge, would_split: false }));
    renderTab();

    fireEvent.click(screen.getByText(/advanced: test a specific identifier/i));
    fireEvent.click(screen.getByText('abc123').closest('button')!);

    // The manual-override (2nd) useSplitPreview call must receive the
    // canonical 'sn:'-prefixed component, not the bare display value —
    // otherwise it never matches the stored component and serials always
    // report "won't split" (the bug this test guards against).
    const manualCalls = preview.mock.calls.filter((args) => args[1] !== undefined);
    expect(manualCalls[manualCalls.length - 1][1]).toBe('sn:abc123');
  });

  it('confirm calls the execute mutation with bridge, the selected primary cluster, and clusters', () => {
    mockPreview(SPLIT_FOUND);
    renderTab();

    // Select the second cluster as primary before confirming — this proves
    // the request body reflects the admin's radio choice, not just index 0.
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2);
    fireEvent.click(radios[1]);

    fireEvent.click(screen.getByRole('button', { name: /split asset/i }));

    expect(mutate).toHaveBeenCalledWith({
      bridge: ['web01'],
      primary_cluster: ['bsc_camera_side2', 'ec:71:db:cb:20:0b'],
      clusters: SPLIT_FOUND.clusters,
    });
  });
});
