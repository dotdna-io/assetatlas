// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ApiError } from '@/api/client';
import { BulkTagDialog } from '../BulkTagDialog';

const mutate = vi.fn();
vi.mock('@/api/assets', () => ({
  useTagBulk: () => ({ mutate, isPending: false }),
}));

// One Alert predicate, spelled as buildFilters() emits it.
const ALERT_FILTER = [
  { field_id: { kind: 'virtual', name: 'alert' }, op: 'eq', value: 'Asset With Public IP Address' },
];

describe('BulkTagDialog', () => {
  // Block body on purpose: an expression-bodied arrow returns the mock, and
  // Vitest treats a hook's return value as the teardown callback — it would
  // call mutate() with no options after every test.
  beforeEach(() => {
    mutate.mockReset();
  });

  it('shows the match count and gates confirm until a tag name is typed', () => {
    render(<BulkTagDialog filters={[]} search="" matchCount={42} onClose={vi.fn()} />);

    expect(screen.getByText(/42 matching assets/i)).toBeInTheDocument();
    const confirm = screen.getByRole('button', { name: 'Tag 42' });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: 'prod' } });
    expect(confirm).not.toBeDisabled();
  });

  it('a whitespace-only name does not enable confirm', () => {
    render(<BulkTagDialog filters={[]} search="" matchCount={5} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: 'Tag 5' })).toBeDisabled();
  });

  it('sends the picked colour, not a silent default', () => {
    render(<BulkTagDialog filters={[]} search="" matchCount={3} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: 'prod' } });
    // ColorPicker labels each swatch with its hex value.
    fireEvent.click(screen.getByRole('radio', { name: '#ef4444' }));
    fireEvent.click(screen.getByRole('button', { name: 'Tag 3' }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0].tag_color).toBe('#ef4444');
  });

  it('trims the tag name before sending it', () => {
    render(<BulkTagDialog filters={[]} search="" matchCount={3} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: '  prod  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tag 3' }));

    expect(mutate.mock.calls[0][0].tag_name).toBe('prod');
  });

  // The alert scope rides in filters[] (#1011): the dialog forwards it by
  // forwarding filters, so the write and the displayed count can't disagree.
  it('forwards the alert filter to the tag mutation', () => {
    render(<BulkTagDialog filters={ALERT_FILTER} search="" matchCount={3} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: 'prod' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tag 3' }));

    expect(mutate.mock.calls[0][0].filters).toEqual(ALERT_FILTER);
    expect(mutate.mock.calls[0][0]).not.toHaveProperty('alert_id');
  });

  // The cap is the primary server-side failure mode; the 400 body carries
  // actionable guidance that must reach the user instead of ApiError.message's
  // debug-style "Request failed: POST … (400)".
  it('surfaces the server cap message instead of the generic request-failed string', () => {
    const friendly = 'Filter matches more than 1000 assets. Narrow your filter…';
    mutate.mockImplementation((_vars, opts) => {
      opts.onError(
        new ApiError('Request failed: POST /api/assets/tags-bulk (400)', 400, {
          message: friendly,
          details: { cap: 1000 },
        }),
      );
    });

    render(<BulkTagDialog filters={[]} search="" matchCount={1500} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: 'prod' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tag 1500' }));

    expect(screen.getByText(friendly)).toBeInTheDocument();
    expect(screen.queryByText(/Request failed/i)).not.toBeInTheDocument();
  });

  it('reports how many of the tagged assets were new', () => {
    mutate.mockImplementation((_vars, opts) => opts.onSuccess({ tagged: 10, created: 4 }));

    render(<BulkTagDialog filters={[]} search="" matchCount={10} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: 'prod' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tag 10' }));

    expect(screen.getByText(/Tagged 10 assets \(4 new\)/i)).toBeInTheDocument();
  });

  it('cancel button calls onClose', () => {
    const onClose = vi.fn();
    render(<BulkTagDialog filters={[]} search="" matchCount={5} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // The delayed close is scheduled on success; unmounting before it fires must
  // not leave a timer that calls onClose on a gone component.
  it('clears the pending close timer on unmount', () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    mutate.mockImplementation((_vars, opts) => opts.onSuccess({ tagged: 1, created: 1 }));

    const { unmount } = render(
      <BulkTagDialog filters={[]} search="" matchCount={1} onClose={onClose} />,
    );
    fireEvent.change(screen.getByLabelText(/tag name/i), { target: { value: 'prod' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tag 1' }));
    unmount();
    vi.runAllTimers();

    expect(onClose).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
