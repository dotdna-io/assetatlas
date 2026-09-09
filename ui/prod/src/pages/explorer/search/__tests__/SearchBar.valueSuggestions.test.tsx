// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { FieldCatalogEntry } from '@/types/field-id';
import { SearchBar } from '../SearchBar';
import { assetsApi } from '@/api/assets';
import type { FieldValuesResponse } from '@/api/assets';

// Value suggestions come from the API; every test drives getFieldValues with
// its own implementation (resolved values, never-resolving promises, rejections).
vi.mock('@/api/assets', () => ({
  assetsApi: {
    getFieldValues: vi.fn(),
    getSemanticSuggestions: vi.fn().mockResolvedValue({ suggestions: [] }),
  },
}));

const getFieldValuesMock = vi.mocked(assetsApi.getFieldValues);

// jsdom does not implement scrollIntoView; SuggestionPanel calls it whenever
// keyboard navigation moves the selection.
Element.prototype.scrollIntoView = vi.fn();

const hostnameField: FieldCatalogEntry = {
  display_name: 'Hostname',
  field_id: { kind: 'static', name: 'Hostname' },
  encoded_key: 'static:Hostname',
  category: 'target',
  data_type: 'string',
  operator_specs: [
    { code: 'eq', label: 'equals', symbol: '=', aliases: ['=', 'is'], arity: 'one', value_type: 'text' },
  ],
  is_array: false,
  is_grouped: false,
  member_sources: null,
};

function valuesResponse(...values: string[]): FieldValuesResponse {
  return {
    field: hostnameField.encoded_key,
    values: values.map((value) => ({ value, count: 1 })),
    total: values.length,
  };
}

function renderSearchBar() {
  const queryClient = new QueryClient();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <SearchBar value="" onChange={vi.fn()} fields={[hostnameField]} synonymLookup={null} />
    </QueryClientProvider>,
  );
  const input = screen.getByPlaceholderText(/search your asset inventory/i) as HTMLInputElement;
  return { input, queryClient, ...result };
}

/** Replace the input content as one keystroke. `text` must put the cursor in a
 *  value context (`hostname = <partial>`), so detectContext routes to the
 *  value-suggestion fetch. */
function typeValue(input: HTMLInputElement, text: string) {
  fireEvent.change(input, { target: { value: text, selectionStart: text.length } });
}

// #1407: the value typeahead fired one /assets/values request per keystroke
// with no debounce, no cache and no cancellation. It must now debounce at
// 250 ms (skipped for an empty partial), dedupe/serve from the react-query
// cache, and abort the superseded in-flight request.
describe('SearchBar — value suggestion debounce, cache and abort', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getFieldValuesMock.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('collapses a 7-character burst into a single request after the debounce', async () => {
    getFieldValuesMock.mockResolvedValue(valuesResponse('abcdefg'));
    const { input } = renderSearchBar();

    const partials = ['a', 'ab', 'abc', 'abcd', 'abcde', 'abcdef', 'abcdefg'];
    for (const partial of partials) {
      typeValue(input, `hostname = ${partial}`);
    }
    expect(getFieldValuesMock).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });

    expect(getFieldValuesMock).toHaveBeenCalledTimes(1);
    expect(getFieldValuesMock).toHaveBeenCalledWith(
      { field: 'static:Hostname', limit: 30, search: 'abcdefg' },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('fetches immediately when the partial is empty (no debounce)', async () => {
    getFieldValuesMock.mockResolvedValue(valuesResponse('alpha'));
    const { input } = renderSearchBar();

    typeValue(input, 'hostname = ');
    // Flush microtasks only — no timer advance. The request must already be out.
    await act(async () => {});

    expect(getFieldValuesMock).toHaveBeenCalledTimes(1);
    expect(getFieldValuesMock).toHaveBeenCalledWith(
      { field: 'static:Hostname', limit: 30, search: undefined },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.getByText('alpha')).toBeTruthy();
  });

  it('serves a re-typed prefix from the cache within staleTime', async () => {
    getFieldValuesMock.mockResolvedValue(valuesResponse('unifi'));
    const { input } = renderSearchBar();

    typeValue(input, 'hostname = un');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(getFieldValuesMock).toHaveBeenCalledTimes(1);

    typeValue(input, 'hostname = uni');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(getFieldValuesMock).toHaveBeenCalledTimes(2);

    // Back to the already-fetched prefix: fresh cache entry, no network call.
    typeValue(input, 'hostname = un');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(getFieldValuesMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText('unifi')).toBeTruthy();
  });

  it('aborts the in-flight request when a new keystroke arrives', async () => {
    const signals: AbortSignal[] = [];
    getFieldValuesMock.mockImplementation((_params, opts) => {
      signals.push(opts!.signal!);
      return new Promise<never>(() => {});
    });
    const { input } = renderSearchBar();

    typeValue(input, 'hostname = u');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(signals).toHaveLength(1);
    expect(signals[0].aborted).toBe(false);

    // The abort happens at the keystroke, before the next debounce fires.
    typeValue(input, 'hostname = un');
    expect(signals[0].aborted).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(signals).toHaveLength(2);
  });

  it('keeps the panel open when a superseded request is aborted', async () => {
    let call = 0;
    getFieldValuesMock.mockImplementation(() => {
      call += 1;
      if (call === 1) return Promise.resolve(valuesResponse('alpha'));
      if (call === 2) return new Promise<never>(() => {}); // superseded in flight
      return Promise.resolve(valuesResponse('beta'));
    });
    const { input } = renderSearchBar();

    // Empty partial: immediate fetch, panel opens with 'alpha'.
    typeValue(input, 'hostname = ');
    await act(async () => {});
    expect(screen.getByText('alpha')).toBeTruthy();

    // 'u' goes out and is aborted by the 'un' keystroke — the abort must not
    // hide the panel or clear the visible suggestions.
    typeValue(input, 'hostname = u');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    typeValue(input, 'hostname = un');
    await act(async () => {});
    expect(screen.getByText('alpha')).toBeTruthy();

    // The replacement fetch resolves and updates the panel.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(screen.getByText('beta')).toBeTruthy();
    expect(getFieldValuesMock).toHaveBeenCalledTimes(3);
  });

  it('hides the panel when the fetch fails with a real error', async () => {
    let call = 0;
    getFieldValuesMock.mockImplementation(() => {
      call += 1;
      return call === 1
        ? Promise.resolve(valuesResponse('alpha'))
        : Promise.reject(new Error('boom'));
    });
    const { input } = renderSearchBar();

    typeValue(input, 'hostname = ');
    await act(async () => {});
    expect(screen.getByText('alpha')).toBeTruthy();

    typeValue(input, 'hostname = u');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(screen.queryByText('alpha')).toBeNull();
  });

  // Round-2 review, scenario S1: the debounced fetch for 'ab' must be
  // cancelled when the trailing space completes the filter — before the fix
  // the timer fired anyway and re-opened the value panel over the finished
  // filter.
  it('cancels the pending fetch when the filter completes inside the debounce window', async () => {
    getFieldValuesMock.mockResolvedValue(valuesResponse('abc'));
    const { input } = renderSearchBar();

    typeValue(input, 'hostname = ab');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(getFieldValuesMock).not.toHaveBeenCalled();

    // Trailing space: the context leaves value mode (filter complete).
    typeValue(input, 'hostname = ab ');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(getFieldValuesMock).not.toHaveBeenCalled();
    expect(screen.queryByText('abc')).toBeNull();
  });

  // Round-2 review, scenario S2: picking a value with Tab+Enter inside the
  // debounce window must cancel the fetch the last keystroke scheduled —
  // before the fix the stale request fired after the pick and re-opened the
  // value panel.
  it('cancels the pending fetch when a value is picked inside the debounce window', async () => {
    getFieldValuesMock.mockResolvedValue(valuesResponse('unifi'));
    const { input } = renderSearchBar();
    // Mirror real usage: the input is already focused while the user types, so
    // the insertion handler's inputRef.focus() is a no-op (a fresh focus event
    // would re-run suggestions against a stale value).
    input.focus();

    // Empty partial fetches immediately and opens the value panel.
    typeValue(input, 'hostname = ');
    await act(async () => {});
    expect(screen.getByText('unifi')).toBeTruthy();

    // The 'u' keystroke schedules the debounced fetch...
    typeValue(input, 'hostname = u');
    // ...and the value is picked before the timer fires.
    fireEvent.keyDown(input, { key: 'Tab' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(input.value).toBe('hostname = unifi ');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    // Only the initial empty-partial request ever went out; the value panel
    // does not re-open afterwards.
    expect(getFieldValuesMock).toHaveBeenCalledTimes(1);
    expect(getFieldValuesMock).toHaveBeenCalledWith(
      { field: 'static:Hostname', limit: 30, search: undefined },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.queryByText('unifi')).toBeNull();
  });

  it('clears the debounce timer and aborts the in-flight request on unmount', async () => {
    // Timer half: unmount BEFORE the 250 ms debounce fires, so the cleanup
    // effect's clearTimeout is what prevents the request — the old version
    // advanced past 250 ms first and never exercised that branch.
    getFieldValuesMock.mockImplementation(() => new Promise<never>(() => {}));
    const first = renderSearchBar();

    typeValue(first.input, 'hostname = u');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(getFieldValuesMock).not.toHaveBeenCalled();

    first.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(getFieldValuesMock).not.toHaveBeenCalled();

    // In-flight half: a request already sent is aborted on unmount.
    const signals: AbortSignal[] = [];
    getFieldValuesMock.mockImplementation((_params, opts) => {
      signals.push(opts!.signal!);
      return new Promise<never>(() => {});
    });
    const second = renderSearchBar();

    typeValue(second.input, 'hostname = u');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(signals).toHaveLength(1);

    second.unmount();
    expect(signals[0].aborted).toBe(true);

    // No pending timer fires another request after unmount.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(signals).toHaveLength(1);
  });
});
