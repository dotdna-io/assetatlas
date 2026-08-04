// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BulkActionsMenu } from '../BulkActionsMenu';

vi.mock('@/api/assets', () => ({
  useTagBulk: () => ({ mutate: vi.fn(), isPending: false }),
  useUntagBulk: () => ({ mutate: vi.fn(), isPending: false }),
  useTagBulkCandidates: () => ({
    data: { candidates: [{ tag_name: 'prod', count: 5 }] },
    isLoading: false,
    error: null,
  }),
  useClassifyBulk: () => ({ mutate: vi.fn(), isPending: false }),
  useClearClassifyBulk: () => ({ mutate: vi.fn(), isPending: false }),
  useCategories: () => ({
    data: { categories: [{ name: 'Network', subcategories: ['Switch'] }] },
    isLoading: false,
  }),
  useAddCustomSubcategory: () => ({ mutate: vi.fn(), isPending: false }),
}));

const open = () => fireEvent.click(screen.getByRole('button', { name: /bulk actions/i }));

describe('BulkActionsMenu', () => {
  it('offers all four filter-scoped actions', () => {
    render(<BulkActionsMenu filters={[]} search="" matchCount={7} />);
    open();

    expect(screen.getByRole('menuitem', { name: /recategorize matching/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /clear category override/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /^tag matching/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /untag matching/i })).toBeInTheDocument();
  });

  it('opens the bulk tag dialog from the Tag matching item', () => {
    render(<BulkActionsMenu filters={[]} search="" matchCount={7} />);
    open();
    fireEvent.click(screen.getByRole('menuitem', { name: /^tag matching/i }));

    expect(screen.getByText('Tag matching assets')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tag 7' })).toBeInTheDocument();
  });

  it('opens the bulk untag dialog from the Untag matching item', () => {
    render(<BulkActionsMenu filters={[]} search="" matchCount={7} />);
    open();
    fireEvent.click(screen.getByRole('menuitem', { name: /untag matching/i }));

    expect(screen.getByText('Untag matching assets')).toBeInTheDocument();
    // The picker, not the apply dialog: a tag list with carrier counts, and a
    // confirm that counts carriers rather than the 7 matches.
    expect(screen.getByRole('option', { name: 'prod (5)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove from 0' })).toBeInTheDocument();
  });

  // matchCount is stale relative to the live filters until the table refetch
  // lands, so the trigger — and with it every item behind it, the new one
  // included — is disabled while the query is in flight. Otherwise the dialog
  // confirms a count that doesn't match the set the server will act on.
  it('is unreachable while the table query is in flight', () => {
    render(<BulkActionsMenu filters={[]} search="" matchCount={7} disabled />);

    expect(screen.getByRole('button', { name: /bulk actions/i })).toBeDisabled();
    open();
    expect(screen.queryByRole('menuitem', { name: /^tag matching/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /untag matching/i })).not.toBeInTheDocument();
  });

  it('is unreachable when nothing matches', () => {
    render(<BulkActionsMenu filters={[]} search="" matchCount={0} />);

    expect(screen.getByRole('button', { name: /bulk actions/i })).toBeDisabled();
  });
});
