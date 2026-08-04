// SPDX-License-Identifier: AGPL-3.0-or-later
// Success feedback contract: a status tagged with `action` is rendered by
// the clicked button (green "Success" flash via Button's `success` prop),
// NOT by the page-wide flash overlay — and untagged statuses keep the
// overlay. If either side of that routing breaks, users get double
// feedback or none at all.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { Button } from '../Button';
import { InlineStatus, successFor } from '../InlineStatus';
import type { InlineStatusValue } from '../InlineStatus';

afterEach(() => {
  vi.useRealTimers();
});

describe('Button success flash', () => {
  it('shows "Success" while the signal is fresh, then restores the label', () => {
    vi.useFakeTimers();
    const signal = { kind: 'success', message: 'Saved', action: 'save' } as const;
    const { rerender } = render(<Button success={null}>Save</Button>);

    rerender(<Button success={signal}>Save</Button>);
    expect(screen.getByRole('button')).toHaveTextContent('Success');
    expect(screen.getByRole('button')).not.toHaveTextContent('Save');

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole('button')).toHaveTextContent('Save');
  });

  it('uses successLabel when given — save buttons read "Saved", not the generic "Success"', () => {
    vi.useFakeTimers();
    const signal: InlineStatusValue = { kind: 'success', message: 'Saved', action: 'save' };
    render(<Button success={signal} successLabel="Saved">Save</Button>);
    expect(screen.getByRole('button')).toHaveTextContent('Saved');
    expect(screen.getByRole('button')).not.toHaveTextContent('Success');
  });

  it('replays the flash when the same action succeeds again (new status identity)', () => {
    vi.useFakeTimers();
    const first: InlineStatusValue = { kind: 'success', message: 'Saved', action: 'save' };
    const { rerender } = render(<Button success={first}>Save</Button>);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole('button')).toHaveTextContent('Save');

    const second: InlineStatusValue = { kind: 'success', message: 'Saved', action: 'save' };
    rerender(<Button success={second}>Save</Button>);
    expect(screen.getByRole('button')).toHaveTextContent('Success');
  });
});

describe('successFor routing', () => {
  const saved: InlineStatusValue = { kind: 'success', message: 'Saved', action: 'save' };

  it('resolves only for the matching action, so sibling buttons stay quiet', () => {
    expect(successFor(saved, 'save')).toBe(saved);
    expect(successFor(saved, 'reset')).toBeNull();
    expect(successFor(null, 'save')).toBeNull();
    expect(successFor({ kind: 'error', message: 'nope', action: 'save' }, 'save')).toBeNull();
  });
});

describe('InlineStatus with button-owned successes', () => {
  it('skips the flash overlay for an action-tagged success but keeps the screen-reader announcement', () => {
    render(<InlineStatus status={{ kind: 'success', message: 'Saved', action: 'save' }} />);
    // Announced for assistive tech…
    expect(screen.getByRole('status')).toHaveTextContent('Saved');
    // …but no overlay pane in the document body.
    expect(document.querySelector('[class*="flashPane"]')).toBeNull();
  });

  it('still renders the flash overlay for untagged successes (dialog-close / no-button flows)', () => {
    render(<InlineStatus status={{ kind: 'success', message: 'Filter saved' }} />);
    expect(document.querySelector('[class*="flashPane"]')).not.toBeNull();
  });
});
