// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { FieldCatalogEntry } from '@/types/field-id';
import { SearchBar } from '../SearchBar';
import type { ComponentProps } from 'react';

// Value suggestions come from the API; the list-operator tests below drive the
// value dropdown, so stub it with a couple of Data Source values.
vi.mock('@/api/assets', () => ({
  assetsApi: {
    getFieldValues: vi.fn().mockResolvedValue({
      values: [
        { value: 'Unifi', count: 5 },
        { value: 'Wazuh', count: 3 },
      ],
    }),
    getSemanticSuggestions: vi.fn().mockResolvedValue({ suggestions: [] }),
  },
}));

// SearchBar reads the react-query client for value suggestions — wrap every
// render in a fresh client.
function renderSearchBar(props: ComponentProps<typeof SearchBar>) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <SearchBar {...props} />
    </QueryClientProvider>,
  );
}

// A text field exposing both value-taking and valueless operators, mirroring a
// live /fields payload.
const hostnameField: FieldCatalogEntry = {
  display_name: 'Hostname',
  field_id: { kind: 'static', name: 'Hostname' },
  encoded_key: 'static:Hostname',
  category: 'target',
  data_type: 'string',
  operator_specs: [
    { code: 'eq', label: 'equals', symbol: '=', aliases: ['=', 'is'], arity: 'one', value_type: 'text' },
    { code: 'empty', label: 'is empty', symbol: 'EMPTY', aliases: ['empty'], arity: 'none', value_type: 'text' },
    { code: 'notempty', label: 'is not empty', symbol: '!EMPTY', aliases: ['!empty'], arity: 'none', value_type: 'text' },
  ],
  is_array: false,
  is_grouped: false,
  member_sources: null,
};

/** Type a field + trailing space so the operator dropdown surfaces, then click
 *  the named operator suggestion. Returns the captured onChange mock. */
function selectOperator(label: string) {
  const onChange = vi.fn();
  renderSearchBar({ value: '', onChange, fields: [hostnameField], synonymLookup: null });
  const input = screen.getByPlaceholderText(/search your asset inventory/i) as HTMLInputElement;

  // "hostname " (field + space) -> operator-suggestion context. Pass an explicit
  // cursor so detectContext sees the text up to the trailing space, not pos 0.
  fireEvent.change(input, { target: { value: 'hostname ', selectionStart: 9 } });

  const opItem = screen.getByText(label);
  // Park the cursor at the end so the insertion math is deterministic in jsdom.
  input.setSelectionRange(input.value.length, input.value.length);
  fireEvent.mouseDown(opItem);

  return onChange;
}

// Selecting a valueless operator (EMPTY / !EMPTY) completes the filter on the
// spot — there is no value step that would otherwise fire onChange. The bug:
// onChange was only fired for value insertions, so the finished filter never
// reached ExplorerPage until the user manually edited the input (e.g. deleted
// the trailing space), at which point the debounced input onChange fired.
describe('SearchBar — valueless operator selection applies the filter', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('fires onChange with an empty filter when EMPTY is picked', () => {
    const onChange = selectOperator('EMPTY is empty');
    expect(onChange).toHaveBeenCalled();
    const parsed = onChange.mock.calls.at(-1)![1];
    expect(parsed.filters).toContainEqual(
      expect.objectContaining({ field: 'hostname', op: 'empty', value: null }),
    );
  });

  it('fires onChange with a notempty filter when !EMPTY is picked', () => {
    const onChange = selectOperator('!EMPTY is not empty');
    expect(onChange).toHaveBeenCalled();
    const parsed = onChange.mock.calls.at(-1)![1];
    expect(parsed.filters).toContainEqual(
      expect.objectContaining({ field: 'hostname', op: 'notempty', value: null }),
    );
  });
});

// A Data Source field exposing a scalar (=) and a list (IN) operator so both
// value-insertion paths can be exercised.
const dataSourceField: FieldCatalogEntry = {
  display_name: 'Data Source',
  field_id: { kind: 'static', name: 'Data Source' },
  encoded_key: 'static:Data%20Source',
  category: 'target',
  data_type: 'string',
  operator_specs: [
    { code: 'eq', label: 'equals', symbol: '=', aliases: ['=', 'is'], arity: 'one', value_type: 'text' },
    { code: 'any', label: 'any of', symbol: 'IN', aliases: ['in'], arity: 'list', value_type: 'text' },
    { code: 'between', label: 'between', symbol: 'BETWEEN', aliases: ['between', 'btw'], arity: 'list', value_type: 'number' },
  ],
  is_array: true,
  is_grouped: false,
  member_sources: null,
};

/** Render, type `prefix` (field + operator + trailing space) so the value
 *  dropdown surfaces, then click the 'Unifi' value. Returns the input's value
 *  after insertion. */
async function pickValueAfter(prefix: string): Promise<string> {
  renderSearchBar({ value: '', onChange: vi.fn(), fields: [dataSourceField], synonymLookup: null });
  const input = screen.getByPlaceholderText(/search your asset inventory/i) as HTMLInputElement;
  // Mirror real usage: the input is already focused while the user types, so the
  // handler's inputRef.focus() is a no-op (a spurious focus event would re-run
  // suggestions against a stale value).
  input.focus();
  fireEvent.change(input, { target: { value: prefix, selectionStart: prefix.length } });
  const unifi = await screen.findByText('Unifi');
  input.setSelectionRange(input.value.length, input.value.length);
  fireEvent.mouseDown(unifi);
  return input.value;
}

// Picking a value for a list operator must chain with a comma so the dropdown
// stays open for the next value; a scalar operator ends the filter with a plain
// space. WHY it matters: without the comma the filter completes after one value
// and IN/ALL/NONE can't build a list from the dropdown at all (the reported bug).
describe('SearchBar — list operator value chaining', () => {
  it('appends ", " after a picked list-operator value', async () => {
    expect(await pickValueAfter('"Data Source" IN ')).toBe('"Data Source" IN Unifi, ');
  });

  it('ends a scalar-operator filter with a plain space (no comma)', async () => {
    expect(await pickValueAfter('"Data Source" = ')).toBe('"Data Source" = Unifi ');
  });

  it('re-opens the value dropdown after a pick so a second value chains', async () => {
    renderSearchBar({ value: '', onChange: vi.fn(), fields: [dataSourceField], synonymLookup: null });
    const input = screen.getByPlaceholderText(/search your asset inventory/i) as HTMLInputElement;
    input.focus();
    fireEvent.change(input, { target: { value: '"Data Source" IN ', selectionStart: 17 } });

    fireEvent.mouseDown(await screen.findByText('Unifi'));
    expect(input.value).toBe('"Data Source" IN Unifi, ');

    // The trailing comma keeps the dropdown in value mode: values are fetched
    // again, so a second value can be picked and it chains onto the list.
    fireEvent.mouseDown(await screen.findByText('Wazuh'));
    expect(input.value).toBe('"Data Source" IN Unifi, Wazuh, ');
  });

  it('caps a between filter at two bounds: comma after the first, space after the second', async () => {
    renderSearchBar({ value: '', onChange: vi.fn(), fields: [dataSourceField], synonymLookup: null });
    const input = screen.getByPlaceholderText(/search your asset inventory/i) as HTMLInputElement;
    input.focus();
    fireEvent.change(input, { target: { value: '"Data Source" between ', selectionStart: 22 } });

    // First bound chains a comma so the second bound can be entered.
    fireEvent.mouseDown(await screen.findByText('Unifi'));
    expect(input.value).toBe('"Data Source" between Unifi, ');

    // Second bound completes the range with a plain space — no third value.
    fireEvent.mouseDown(await screen.findByText('Wazuh'));
    expect(input.value).toBe('"Data Source" between Unifi, Wazuh ');
  });
});
