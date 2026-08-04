// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ApiError } from '@/api/client';
import { BulkUntagDialog } from '../BulkUntagDialog';

const mutate = vi.fn();
const candidatesQuery = vi.fn();

vi.mock('@/api/assets', () => ({
  useTagBulkCandidates: (...args: unknown[]) => candidatesQuery(...args),
  useUntagBulk: () => ({ mutate, isPending: false }),
}));

const LOADED = {
  data: { candidates: [{ tag_name: 'prod', count: 847 }, { tag_name: 'stale', count: 3 }] },
  isLoading: false,
  error: null,
};

// One Alert predicate, spelled as buildFilters() emits it.
const ALERT_FILTER = [
  { field_id: { kind: 'virtual', name: 'alert' }, op: 'eq', value: 'Asset With Public IP Address' },
];

const pick = (tag: string) =>
  fireEvent.change(screen.getByLabelText(/^tag$/i), { target: { value: tag } });

describe('BulkUntagDialog', () => {
  // Block body on purpose: an expression-bodied arrow returns the mock, and
  // Vitest treats a hook's return value as the teardown callback — it would
  // call mutate() with no options after every test.
  beforeEach(() => {
    mutate.mockReset();
    candidatesQuery.mockReset();
    candidatesQuery.mockReturnValue(LOADED);
  });

  it('lists each manual tag with its carrier count, nothing preselected', () => {
    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);

    expect(screen.getByRole('option', { name: 'prod (847)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'stale (3)' })).toBeInTheDocument();
    expect(screen.getByLabelText(/^tag$/i)).toHaveValue('');
  });

  // The whole point of the picker: only manual rows can be deleted, so it shows
  // exactly what the server returned from asset_manual_tags and never invents
  // an entry from the (union) tag catalog Explorer filters on.
  it('offers only what the candidates endpoint returned', () => {
    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);

    const options = screen.getAllByRole('option').map((o) => o.textContent);
    expect(options).toHaveLength(3); // placeholder + the two candidates
    expect(options).toEqual(expect.arrayContaining(['prod (847)', 'stale (3)']));
  });

  it('scopes the picker to the live filters, alert scope included', () => {
    render(<BulkUntagDialog filters={ALERT_FILTER} search="printer" matchCount={9} onClose={vi.fn()} />);

    // Counts scoped to the fleet instead of the drilldown would make the
    // confirm button promise a number the removal cannot deliver.
    expect(candidatesQuery).toHaveBeenCalledWith({ filters: ALERT_FILTER, search: 'printer' });
  });

  // Carriers, not matches: saying "Remove from 1000" when 847 carry the tag is
  // a lie the user notices right afterwards.
  it('counts the selected tag’s carriers, not the matched assets', () => {
    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Remove from 0' })).toBeDisabled();
    pick('prod');
    expect(screen.getByRole('button', { name: 'Remove from 847' })).toBeEnabled();
    pick('stale');
    expect(screen.getByRole('button', { name: 'Remove from 3' })).toBeInTheDocument();
  });

  it('sends the picked tag with the live filters and no colour', () => {
    render(<BulkUntagDialog filters={ALERT_FILTER} search="printer" matchCount={9} onClose={vi.fn()} />);
    pick('prod');
    fireEvent.click(screen.getByRole('button', { name: 'Remove from 847' }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({
      filters: ALERT_FILTER,
      search: 'printer',
      tag_name: 'prod',
    });
  });

  it('reports how many assets actually lost the tag', () => {
    mutate.mockImplementation((_vars, opts) => opts.onSuccess({ removed: 847 }));

    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);
    pick('prod');
    fireEvent.click(screen.getByRole('button', { name: 'Remove from 847' }));

    expect(screen.getByText(/Removed .*prod.* from 847 assets/i)).toBeInTheDocument();
  });

  it('clears the selection once the removal lands', () => {
    // useUntagBulk invalidates ['assets'], which covers the candidates query,
    // so the refetch drops the tag just removed. Holding the old selection
    // through the 900ms close delay would leave the select blank with the
    // confirm still enabled at "Remove from 0" — one more click and the user
    // fires a second, guaranteed no-op removal.
    mutate.mockImplementation((_vars, opts) => opts.onSuccess({ removed: 847 }));

    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);
    pick('prod');
    fireEvent.click(screen.getByRole('button', { name: 'Remove from 847' }));

    expect(screen.getByLabelText(/^tag$/i)).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Remove from 0' })).toBeDisabled();
  });

  // ------------------------------------------------------------------
  // Loading / empty / error — all three are real states here, because the
  // picker has to load before the dialog can offer anything.
  // ------------------------------------------------------------------

  it('disables the picker while the candidates load', () => {
    candidatesQuery.mockReturnValue({ data: undefined, isLoading: true, error: null });
    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);

    expect(screen.getByLabelText(/^tag$/i)).toBeDisabled();
    expect(screen.getByRole('button', { name: /^remove from/i })).toBeDisabled();
    expect(screen.getByRole('option', { name: /loading/i })).toBeInTheDocument();
  });

  it('explains an empty picker instead of offering an unusable confirm', () => {
    candidatesQuery.mockReturnValue({ data: { candidates: [] }, isLoading: false, error: null });
    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);

    // Says why it is empty: rule-applied tags live in another table and come
    // back on the next pipeline run, so they are not removable here.
    expect(screen.getByText(/no manual tags on the matching assets/i)).toBeInTheDocument();
    expect(screen.getByText(/rule-based tags cannot be removed here/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^remove from/i })).toBeDisabled();
  });

  // The cap is enforced on the candidates load as well as on the removal, so
  // its actionable message has to reach the user from here too rather than
  // ApiError.message's debug-style "Request failed: GET … (400)".
  it('surfaces the server cap message from the candidates load', () => {
    const friendly = 'Filter matches more than 1000 assets. Narrow your filter…';
    candidatesQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new ApiError('Request failed: GET /api/assets/tags-bulk/candidates (400)', 400, {
        message: friendly,
        details: { cap: 1000 },
      }),
    });

    render(<BulkUntagDialog filters={[]} search="" matchCount={1500} onClose={vi.fn()} />);

    expect(screen.getByText(friendly)).toBeInTheDocument();
    expect(screen.queryByText(/Request failed/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^remove from/i })).toBeDisabled();
    // A failed load carries no candidates, but saying "No manual tags" here
    // would contradict the error right above it and read as a fact about the
    // matched assets rather than about the request.
    expect(screen.getByRole('option', { name: /tags unavailable/i })).toBeInTheDocument();
    expect(screen.queryByText(/no manual tags/i)).not.toBeInTheDocument();
  });

  it('surfaces the server message when the removal itself fails', () => {
    const friendly = 'Filter matches more than 1000 assets. Narrow your filter…';
    mutate.mockImplementation((_vars, opts) => {
      opts.onError(
        new ApiError('Request failed: POST /api/assets/tags-bulk/remove (400)', 400, {
          message: friendly,
          details: { cap: 1000 },
        }),
      );
    });

    render(<BulkUntagDialog filters={[]} search="" matchCount={1000} onClose={vi.fn()} />);
    pick('prod');
    fireEvent.click(screen.getByRole('button', { name: 'Remove from 847' }));

    expect(screen.getByText(friendly)).toBeInTheDocument();
    expect(screen.queryByText(/Request failed/i)).not.toBeInTheDocument();
  });

  // ------------------------------------------------------------------
  // Close paths
  // ------------------------------------------------------------------

  it('cancel button calls onClose', () => {
    const onClose = vi.fn();
    render(<BulkUntagDialog filters={[]} search="" matchCount={5} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // The delayed close is scheduled on success; unmounting before it fires must
  // not leave a timer that calls onClose on a gone component.
  it('clears the pending close timer on unmount', () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    mutate.mockImplementation((_vars, opts) => opts.onSuccess({ removed: 1 }));

    const { unmount } = render(
      <BulkUntagDialog filters={[]} search="" matchCount={1} onClose={onClose} />,
    );
    pick('prod');
    fireEvent.click(screen.getByRole('button', { name: 'Remove from 847' }));
    unmount();
    vi.runAllTimers();

    expect(onClose).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
