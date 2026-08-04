// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ApiError } from '@/api/client';
import { BulkRecategorizeDialog } from '../BulkRecategorizeDialog';

const mutate = vi.fn();
const clearMutate = vi.fn();
vi.mock('@/api/assets', () => ({
  useClassifyBulk: () => ({ mutate, isPending: false }),
  useClearClassifyBulk: () => ({ mutate: clearMutate, isPending: false }),
  useCategories: () => ({
    data: { categories: [{ name: 'Network', subcategories: ['Switch'] }] },
    isLoading: false,
  }),
  useAddCustomSubcategory: () => ({ mutate: vi.fn(), isPending: false }),
}));

// One Alert predicate, spelled as buildFilters() emits it.
const ALERT_FILTER = [
  { field_id: { kind: 'virtual', name: 'alert' }, op: 'eq', value: 'Asset With Public IP Address' },
];

describe('BulkRecategorizeDialog', () => {
  beforeEach(() => {
    mutate.mockReset();
    clearMutate.mockReset();
  });

  it('shows the match count and gates confirm until a selection is made', () => {
    render(
      <BulkRecategorizeDialog
        mode="recategorize"
        filters={[]}
        search=""
        matchCount={42}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/42 matching assets/i)).toBeInTheDocument();
    const confirm = screen.getByRole('button', { name: /apply|recategorize/i });
    expect(confirm).toBeDisabled(); // no category/subcategory chosen yet
  });

  it('clear mode shows warning text and an enabled confirm button', () => {
    render(
      <BulkRecategorizeDialog
        mode="clear"
        filters={[]}
        search=""
        matchCount={15}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/15 matching assets/i)).toBeInTheDocument();
    // clear mode confirm should be enabled (no selection required)
    const confirm = screen.getByRole('button', { name: /reset/i });
    expect(confirm).not.toBeDisabled();
  });

  it('cancel button calls onClose', () => {
    const onClose = vi.fn();
    render(
      <BulkRecategorizeDialog
        mode="recategorize"
        filters={[]}
        search=""
        matchCount={5}
        onClose={onClose}
      />,
    );
    screen.getByRole('button', { name: /cancel/i }).click();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // The cap (>1000 assets) is the spec's primary failure mode. The server
  // returns a 400 whose body.message carries actionable guidance; the dialog
  // must surface that via getApiErrorMessage, NOT the debug-style
  // "Request failed: POST … (400)" text that lives on ApiError.message.
  it('surfaces the server cap message instead of the generic request-failed string', () => {
    const friendly = 'Filter matches more than 1000 assets. Narrow your filter…';
    mutate.mockImplementation((_vars, opts) => {
      opts.onError(
        new ApiError('Request failed: POST /api/assets/classify-bulk (400)', 400, {
          message: friendly,
          details: { cap: 1000 },
        }),
      );
    });

    render(
      <BulkRecategorizeDialog
        mode="recategorize"
        filters={[]}
        search=""
        matchCount={1500}
        onClose={vi.fn()}
      />,
    );

    // Pick category + subcategory so the confirm button enables.
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'Network' } });
    fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'Switch' } });
    fireEvent.click(screen.getByRole('button', { name: /recategorize/i }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(screen.getByText(friendly)).toBeInTheDocument();
    expect(screen.queryByText(/Request failed/i)).not.toBeInTheDocument();
  });

  // The server's own count must stay on screen. Tagging the success with
  // `action:` hands the visible confirmation to the button and renders the
  // InlineStatus line screen-reader-only, so if the count is not on the
  // button's success label it is not visible anywhere. It cannot be read off
  // matchCount either: the server re-evaluates the filter, so the two differ
  // exactly when the difference is worth showing.
  it('shows the count the server reports, not the pre-flight estimate', () => {
    mutate.mockImplementation((_vars, opts) => opts.onSuccess({ updated: 37 }));

    render(
      <BulkRecategorizeDialog
        mode="recategorize"
        filters={[]}
        search=""
        matchCount={42}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'Network' } });
    fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'Switch' } });
    fireEvent.click(screen.getByRole('button', { name: /recategorize/i }));

    expect(screen.getByRole('button', { name: /Recategorized 37/ })).toBeInTheDocument();
  });

  it('shows the cleared count on the reset button', () => {
    clearMutate.mockImplementation((_vars, opts) => opts.onSuccess({ cleared: 9 }));

    render(
      <BulkRecategorizeDialog
        mode="clear"
        filters={[]}
        search=""
        matchCount={15}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /reset/i }));

    expect(screen.getByRole('button', { name: /Reset 9/ })).toBeInTheDocument();
  });

  // The alert scope must reach the WRITE, not just the count (#1011). It rides
  // in filters[] now, so the dialog forwards it by forwarding filters — there is
  // no separate parameter left to drop, and therefore no way for the write and
  // the displayed count to disagree.
  it('forwards the alert filter to the recategorize mutation', () => {
    render(
      <BulkRecategorizeDialog
        mode="recategorize"
        filters={ALERT_FILTER}
        search=""
        matchCount={3}
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'Network' } });
    fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'Switch' } });
    fireEvent.click(screen.getByRole('button', { name: /recategorize/i }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0].filters).toEqual(ALERT_FILTER);
    expect(mutate.mock.calls[0][0]).not.toHaveProperty('alert_id');
  });

  it('forwards the alert filter to the clear mutation', () => {
    render(
      <BulkRecategorizeDialog
        mode="clear"
        filters={ALERT_FILTER}
        search=""
        matchCount={3}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /reset/i }));

    expect(clearMutate).toHaveBeenCalledTimes(1);
    expect(clearMutate.mock.calls[0][0].filters).toEqual(ALERT_FILTER);
    expect(clearMutate.mock.calls[0][0]).not.toHaveProperty('alert_id');
  });
});
