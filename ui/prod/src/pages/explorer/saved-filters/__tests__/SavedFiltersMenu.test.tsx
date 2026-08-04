// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SavedFiltersMenu } from '../SavedFiltersMenu';
import type { SavedFilter } from '@/api/saved-filters';

// The menu never reads the snapshot, but SavedFilter requires a payload — give a
// minimal valid one so these stay type-safe without casts.
const payload: SavedFilter['payload'] = {
  version: 1,
  searchQuery: '',
  columnFilters: {},
  dateFilters: {},
  sort: { column: '', direction: 'asc' },
  columns: { order: [], visible: {}, widths: {} },
  pivot: {
    mode: false,
    config: { rowDimension: '', columnDimension: '', columnsEnabled: false, displayMode: 'count' },
  },
};

function mk(over: Partial<SavedFilter> = {}): SavedFilter {
  return {
    id: 'id', name: 'Filter', description: null, payload,
    visibility: 'private', access: 'owner', can_edit: true,
    created_at: '', updated_at: '',
    ...over,
  };
}

const noop = vi.fn();
const base = {
  isOpen: true,
  onClose: noop,
  loading: false,
  error: false,
  onSaveCurrent: noop,
  onLoad: noop,
  onShare: noop,
  onRename: noop,
  onDelete: noop,
};

describe('SavedFiltersMenu — Library section', () => {
  it('renders the Library section with published filters', () => {
    render(<SavedFiltersMenu {...base} filters={[]} library={[mk({ id: 'l1', name: 'Fleet', access: 'org', can_edit: false })]} />);
    expect(screen.getByText('Library')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Fleet' })).toBeInTheDocument();
  });

  it('omits the Library section when empty (no bare header)', () => {
    render(<SavedFiltersMenu {...base} filters={[mk({ name: 'Mine' })]} library={[]} />);
    expect(screen.queryByText('Library')).not.toBeInTheDocument();
  });

  it('library items are load-only while owned filters keep share/edit/delete', () => {
    // Encodes D6: non-owners can view + load a published filter but never edit
    // it in place, so the Library row must expose no share/rename/delete affordances.
    const onLoad = vi.fn();
    const mine = mk({ id: 'm1', name: 'Mine', can_edit: true });
    const lib = mk({ id: 'l1', name: 'Fleet', access: 'org', can_edit: false });
    render(<SavedFiltersMenu {...base} filters={[mine]} library={[lib]} onLoad={onLoad} />);

    // Owned filter keeps all three affordances.
    expect(screen.getByRole('button', { name: /share mine/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /edit mine/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete mine/i })).toBeInTheDocument();
    // Library filter is load-only.
    expect(screen.queryByRole('button', { name: /share fleet/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /edit fleet/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /delete fleet/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
    expect(onLoad).toHaveBeenCalledWith(lib);
  });

  it('Share button on an owned filter calls onShare', () => {
    const onShare = vi.fn();
    const mine = mk({ id: 'm1', name: 'Mine' });
    render(<SavedFiltersMenu {...base} filters={[mine]} library={[]} onShare={onShare} />);
    fireEvent.click(screen.getByRole('button', { name: /share mine/i }));
    expect(onShare).toHaveBeenCalledWith(mine);
  });
});
