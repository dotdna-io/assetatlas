// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { MergeDenylistTab } from '../MergeDenylistTab';
import {
  useMergeDenylist,
  useCreateDenylistEntry,
  useDeleteDenylistEntry,
  useDenylistMatchPreview,
} from '@/api/merge-denylist';
import type { DenylistEntry } from '@/api/merge-denylist';

// Mock the hooks only — same approach as AlertRoutingTab.test.tsx /
// UnifiedFieldsTab.test.tsx — so the component renders without a live
// backend (useMergeDenylist would otherwise sit in isLoading forever).
// MATCH_TYPES_BY_TYPE comes through from `actual`: it is the pair rule the
// selector is built from, so a stub of it would test nothing.
vi.mock('@/api/merge-denylist', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/merge-denylist')>();
  return {
    ...actual,
    useMergeDenylist: vi.fn(),
    useCreateDenylistEntry: vi.fn(),
    useDeleteDenylistEntry: vi.fn(),
    useDenylistMatchPreview: vi.fn(),
  };
});

const mockUseMergeDenylist = useMergeDenylist as ReturnType<typeof vi.fn>;
const mockUseCreateDenylistEntry = useCreateDenylistEntry as ReturnType<typeof vi.fn>;
const mockUseDeleteDenylistEntry = useDeleteDenylistEntry as ReturnType<typeof vi.fn>;
const mockUseDenylistMatchPreview = useDenylistMatchPreview as ReturnType<typeof vi.fn>;

function optionLabels(name: RegExp): string[] {
  return Array.from(
    screen.getByRole('combobox', { name }).querySelectorAll('option'),
  ).map((o) => o.textContent);
}

/** The form debounces before it builds a preview draft. */
function settleDebounce() {
  act(() => {
    vi.advanceTimersByTime(400);
  });
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderTab() {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MergeDenylistTab />
    </QueryClientProvider>,
  );
}

const ENTRY: DenylistEntry = {
  id: 'd1',
  identifier_type: 'hostname',
  match_type: 'exact',
  identifier_value: 'web01',
  raw_value: 'web01',
  reason: 'reused after decommission',
  created_by: 'admin',
  enabled: true,
  created_at: '2026-07-01T00:00:00Z',
};

const NO_PREVIEW = { data: undefined, isPending: false, isError: false, error: null };

describe('MergeDenylistTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseMergeDenylist.mockReturnValue({ data: { entries: [] }, isLoading: false });
    mockUseCreateDenylistEntry.mockReturnValue({ mutate: vi.fn(), isPending: false });
    mockUseDeleteDenylistEntry.mockReturnValue({ mutate: vi.fn(), isPending: false });
    mockUseDenylistMatchPreview.mockReturnValue(NO_PREVIEW);
  });

  it('renders the add form', () => {
    renderTab();
    expect(screen.getByPlaceholderText(/identifier/i)).toBeInTheDocument();
  });

  it('states plainly that the denylist is for shared identifiers only', () => {
    renderTab();
    expect(
      screen.getByText(/denylisting a device's only identifier will drop it/i),
    ).toBeInTheDocument();
  });

  it('shows an empty-state row when there are no entries', () => {
    renderTab();
    expect(screen.getByText(/no denylisted identifiers/i)).toBeInTheDocument();
  });

  it('renders a row for each denylist entry', () => {
    mockUseMergeDenylist.mockReturnValue({ data: { entries: [ENTRY] }, isLoading: false });
    renderTab();

    expect(screen.getByText('web01')).toBeInTheDocument();
    expect(screen.getByText('reused after decommission')).toBeInTheDocument();
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  it('shows each identifier type as a word, not as its wire value (#973)', () => {
    // TYPE_LABELS is all that stands between the admin and the API's own
    // spelling — 'fqdn' and 'mac' are what the wire carries, 'FQDN' and 'MAC'
    // are what the screen should say. Both places a type is rendered read from
    // it, so a type added to TYPES alone would render an empty cell here rather
    // than fail anywhere.
    mockUseMergeDenylist.mockReturnValue({
      data: {
        entries: [
          { ...ENTRY, id: 'f1', identifier_type: 'fqdn', raw_value: 'web01.corp.local' },
          { ...ENTRY, id: 'm1', identifier_type: 'mac', raw_value: 'bc:24:11:22:33:44' },
        ],
      },
      isLoading: false,
    });
    renderTab();

    expect(optionLabels(/identifier type/i)).toEqual(['Hostname', 'FQDN', 'MAC', 'Serial']);

    // Scoped to the table: the same words are also the selector's options.
    const list = within(screen.getByRole('table'));
    expect(list.getByText('FQDN')).toBeInTheDocument();
    expect(list.getByText('MAC')).toBeInTheDocument();
  });

  it('disables Add until a value is entered, then calls create with the form fields', () => {
    const mutate = vi.fn();
    mockUseCreateDenylistEntry.mockReturnValue({ mutate, isPending: false });
    renderTab();

    const addButton = screen.getByRole('button', { name: /^add$/i });
    expect(addButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/identifier/i), {
      target: { value: 'web01' },
    });
    expect(addButton).not.toBeDisabled();

    fireEvent.click(addButton);
    expect(mutate).toHaveBeenCalledWith({
      identifier_type: 'hostname',
      match_type: 'exact',
      raw_value: 'web01',
      reason: null,
    });
  });

  it('confirms before removing an entry, then calls delete with its id', () => {
    mockUseMergeDenylist.mockReturnValue({ data: { entries: [ENTRY] }, isLoading: false });
    const mutate = vi.fn();
    mockUseDeleteDenylistEntry.mockReturnValue({ mutate, isPending: false });
    renderTab();

    fireEvent.click(screen.getByRole('button', { name: /remove web01/i }));
    expect(screen.getByText(/Remove denylist entry/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^remove$/i }));
    expect(mutate).toHaveBeenCalledWith('d1');
  });

  it('shows a loading spinner instead of the form while the list is loading', () => {
    mockUseMergeDenylist.mockReturnValue({ data: undefined, isLoading: true });
    renderTab();

    expect(screen.queryByPlaceholderText(/identifier/i)).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: /loading/i })).toBeInTheDocument();
  });

  // ── Pattern entries (#991) ───────────────────────────────────────────────

  describe('match types', () => {
    // The pair rule is enforced three times over on the backend (Pydantic, the
    // encoder, migration 135's CHECK). The selector's job is to never let an
    // admin reach any of them.
    it.each([
      ['hostname', ['Exact', 'Starts with', 'Ends with']],
      ['fqdn', ['Exact', 'Starts with', 'Ends with']],
      ['mac', ['Exact', 'Starts with', 'Range']],
      ['serial', ['Exact']],
    ])('offers only the match types %s supports', (type, expected) => {
      renderTab();
      fireEvent.change(screen.getByRole('combobox', { name: /identifier type/i }), {
        target: { value: type },
      });
      expect(optionLabels(/match type/i)).toEqual(expected);
    });

    it('drops back to Exact when the chosen match type is illegal for the new type', () => {
      // A MAC range left selected while switching to serial would be a 400 the
      // admin never asked for.
      renderTab();
      const typeSelect = screen.getByRole('combobox', { name: /identifier type/i });
      fireEvent.change(typeSelect, { target: { value: 'mac' } });
      fireEvent.change(screen.getByRole('combobox', { name: /match type/i }), {
        target: { value: 'range' },
      });
      expect(screen.getByLabelText(/range start/i)).toBeInTheDocument();

      fireEvent.change(typeSelect, { target: { value: 'serial' } });
      expect(screen.getByRole('combobox', { name: /match type/i })).toHaveValue('exact');
      expect(screen.queryByLabelText(/range start/i)).not.toBeInTheDocument();
    });

    it('keeps a match type that is legal for both types', () => {
      renderTab();
      fireEvent.change(screen.getByRole('combobox', { name: /match type/i }), {
        target: { value: 'prefix' },
      });
      fireEvent.change(screen.getByRole('combobox', { name: /identifier type/i }), {
        target: { value: 'fqdn' },
      });
      expect(screen.getByRole('combobox', { name: /match type/i })).toHaveValue('prefix');
    });
  });

  describe('create body shape', () => {
    // _check_entry_shape 400s if a range carries a raw_value or a non-range
    // carries bounds, so the two shapes are built, never blanked.
    it('sends raw_value and no bounds for a prefix', () => {
      const mutate = vi.fn();
      mockUseCreateDenylistEntry.mockReturnValue({ mutate, isPending: false });
      renderTab();

      fireEvent.change(screen.getByRole('combobox', { name: /identifier type/i }), {
        target: { value: 'mac' },
      });
      fireEvent.change(screen.getByRole('combobox', { name: /match type/i }), {
        target: { value: 'prefix' },
      });
      fireEvent.change(screen.getByLabelText(/identifier value/i), {
        target: { value: ' bc:24:11:* ' },
      });
      fireEvent.change(screen.getByLabelText(/^reason$/i), {
        target: { value: 'proxmox guest veth' },
      });
      fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

      expect(mutate).toHaveBeenCalledWith({
        identifier_type: 'mac',
        match_type: 'prefix',
        raw_value: 'bc:24:11:*',
        reason: 'proxmox guest veth',
      });
      const body = mutate.mock.calls[0][0];
      expect(body).not.toHaveProperty('range_start');
      expect(body).not.toHaveProperty('range_end');
    });

    it('sends both bounds and no raw_value for a range', () => {
      const mutate = vi.fn();
      mockUseCreateDenylistEntry.mockReturnValue({ mutate, isPending: false });
      renderTab();

      fireEvent.change(screen.getByRole('combobox', { name: /identifier type/i }), {
        target: { value: 'mac' },
      });
      fireEvent.change(screen.getByRole('combobox', { name: /match type/i }), {
        target: { value: 'range' },
      });

      const addButton = screen.getByRole('button', { name: /^add$/i });
      expect(addButton).toBeDisabled();
      fireEvent.change(screen.getByLabelText(/range start/i), {
        target: { value: 'bc:24:11:00:00:00' },
      });
      // One bound is not a range.
      expect(addButton).toBeDisabled();
      fireEvent.change(screen.getByLabelText(/range end/i), {
        target: { value: 'bc:24:11:ff:ff:ff' },
      });
      expect(addButton).not.toBeDisabled();
      fireEvent.click(addButton);

      expect(mutate).toHaveBeenCalledWith({
        identifier_type: 'mac',
        match_type: 'range',
        range_start: 'bc:24:11:00:00:00',
        range_end: 'bc:24:11:ff:ff:ff',
        reason: null,
      });
      expect(mutate.mock.calls[0][0]).not.toHaveProperty('raw_value');
    });
  });

  it('shows each entry’s match type and the pattern the admin typed', () => {
    mockUseMergeDenylist.mockReturnValue({
      data: {
        entries: [
          { ...ENTRY, id: 'p1', identifier_type: 'mac', match_type: 'prefix', raw_value: 'bc:24:11:*' },
          {
            ...ENTRY,
            id: 'r1',
            identifier_type: 'mac',
            match_type: 'range',
            // The service's display spelling for a range — the bounds are not
            // round-tripped as numbers, so this string is the whole story.
            raw_value: 'BC:24:11:00:00:00 - BC:24:11:FF:FF:FF',
          },
          { ...ENTRY, id: 's1', match_type: 'suffix', raw_value: '*.corp.local' },
        ],
      },
      isLoading: false,
    });
    renderTab();

    // Scoped to the table: the same words are also the selector's options.
    const list = within(screen.getByRole('table'));
    expect(list.getByText('bc:24:11:*')).toBeInTheDocument();
    expect(list.getByText('Starts with')).toBeInTheDocument();
    expect(list.getByText('BC:24:11:00:00:00 - BC:24:11:FF:FF:FF')).toBeInTheDocument();
    expect(list.getByText('Range')).toBeInTheDocument();
    expect(list.getByText('*.corp.local')).toBeInTheDocument();
    expect(list.getByText('Ends with')).toBeInTheDocument();
  });

  describe('match-count preview', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    function typePrefix(raw: string) {
      fireEvent.change(screen.getByRole('combobox', { name: /identifier type/i }), {
        target: { value: 'mac' },
      });
      fireEvent.change(screen.getByRole('combobox', { name: /match type/i }), {
        target: { value: 'prefix' },
      });
      fireEvent.change(screen.getByLabelText(/identifier value/i), { target: { value: raw } });
      settleDebounce();
    }

    it('asks for no preview until the entry is complete', () => {
      renderTab();
      settleDebounce();
      // A null draft is what disables the queries — an empty form must not
      // fire one per keystroke.
      expect(mockUseDenylistMatchPreview).toHaveBeenLastCalledWith(null);
    });

    it('passes the debounced draft to the preview once a value is typed', () => {
      renderTab();
      typePrefix('bc:24:11:*');

      expect(mockUseDenylistMatchPreview).toHaveBeenLastCalledWith({
        identifier_type: 'mac',
        match_type: 'prefix',
        raw_value: 'bc:24:11:*',
        range_start: '',
        range_end: '',
      });
    });

    it('reports how much of the fleet the entry would take out', () => {
      mockUseDenylistMatchPreview.mockReturnValue({
        ...NO_PREVIEW,
        data: { matched: 412, total: 25913, capped: false },
      });
      renderTab();
      typePrefix('bc:24:11:*');

      expect(screen.getByText(/Matches 412 of your 25,913 MAC addresses/i)).toBeInTheDocument();
    });

    it('calls a typo out as matching nothing rather than staying silent', () => {
      mockUseDenylistMatchPreview.mockReturnValue({
        ...NO_PREVIEW,
        data: { matched: 0, total: 25913, capped: false },
      });
      renderTab();
      typePrefix('bc:24:11:*');

      expect(screen.getByText(/Matches none of your 25,913 MAC addresses/i)).toBeInTheDocument();
    });

    it('reports a truncated count as a floor, not a total', () => {
      // 500 back means the cap was hit, so the true count is >= 500. Claiming
      // exactly 500 would understate the very entry this is meant to catch.
      mockUseDenylistMatchPreview.mockReturnValue({
        ...NO_PREVIEW,
        data: { matched: 500, total: 25913, capped: true },
      });
      renderTab();
      typePrefix('bc:24:11:*');

      expect(
        screen.getByText(/Matches at least 500 of your 25,913 MAC addresses/i),
      ).toBeInTheDocument();
    });

    it('refuses to put a floor on a truncated range, which could read as safe', () => {
      // A range is narrowed only by the prefix enclosing it, which can be far
      // wider than the range: 30 of a truncated 500 says nothing about how many
      // of the other 25,000 are in range. Every other match type narrows to
      // real matches, so its floor is honest.
      mockUseDenylistMatchPreview.mockReturnValue({
        ...NO_PREVIEW,
        data: { matched: 30, total: 25913, capped: true },
      });
      renderTab();
      fireEvent.change(screen.getByRole('combobox', { name: /identifier type/i }), {
        target: { value: 'mac' },
      });
      fireEvent.change(screen.getByRole('combobox', { name: /match type/i }), {
        target: { value: 'range' },
      });
      fireEvent.change(screen.getByLabelText(/range start/i), {
        target: { value: '00:00:00:00:00:00' },
      });
      fireEvent.change(screen.getByLabelText(/range end/i), {
        target: { value: 'ff:ff:ff:ff:ff:ff' },
      });
      settleDebounce();

      expect(screen.getByText(/too many MAC addresses to count exactly/i)).toBeInTheDocument();
      expect(screen.queryByText(/at least 30/i)).not.toBeInTheDocument();
    });

    it('surfaces a failed preview without blocking the add', () => {
      // Through getApiErrorMessage, so a 400 from /assets/values reads as
      // itself. The preview is advisory: a fleet too big to count is not a
      // reason to refuse the entry.
      mockUseDenylistMatchPreview.mockReturnValue({
        ...NO_PREVIEW,
        isError: true,
        error: new Error('Field is not filterable'),
      });
      renderTab();
      typePrefix('bc:24:11:*');

      expect(screen.getByText(/Field is not filterable/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^add$/i })).not.toBeDisabled();
    });

    it('falls back to a plain message when the failure carries none', () => {
      mockUseDenylistMatchPreview.mockReturnValue({ ...NO_PREVIEW, isError: true, error: null });
      renderTab();
      typePrefix('bc:24:11:*');

      expect(screen.getByText(/Match preview unavailable/i)).toBeInTheDocument();
    });
  });
});
