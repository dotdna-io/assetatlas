// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useDebouncedValue } from '../useDebouncedValue';

describe('useDebouncedValue', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('seeds to the initial value with no delay', () => {
    const { result } = renderHook(() => useDebouncedValue('a', 500));
    expect(result.current).toBe('a');
  });

  it('delays updates until the value has been stable for delayMs', () => {
    const { result, rerender } = renderHook(
      ({ v }) => useDebouncedValue(v, 500),
      { initialProps: { v: 'a' } },
    );

    rerender({ v: 'b' });
    expect(result.current).toBe('a'); // not yet elapsed

    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(result.current).toBe('a'); // one tick short

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe('b'); // settled at exactly delayMs
  });

  it('resets the timer on rapid changes so only the last value lands', () => {
    const { result, rerender } = renderHook(
      ({ v }) => useDebouncedValue(v, 500),
      { initialProps: { v: 'a' } },
    );

    rerender({ v: 'b' });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    rerender({ v: 'c' }); // resets the 500ms window before b could settle
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(result.current).toBe('a'); // neither b nor c has settled

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe('c'); // c wins; b was superseded
  });

  it('clears the pending timer on unmount', () => {
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout');
    const { rerender, unmount } = renderHook(
      ({ v }) => useDebouncedValue(v, 500),
      { initialProps: { v: 'a' } },
    );

    rerender({ v: 'b' }); // schedules a timer
    const before = clearSpy.mock.calls.length;
    unmount();
    expect(clearSpy.mock.calls.length).toBeGreaterThan(before);
    clearSpy.mockRestore();
  });
});
