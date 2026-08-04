// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ColumnFilter } from '../ColumnFilter';
import { assetsApi } from '@/api/assets';

vi.mock('@/api/assets', () => ({
  assetsApi: { getFieldValues: vi.fn() },
}));

const VALUES = {
  values: [
    { value: 'Workstation', count: 142 },
    { value: 'Server', count: 88 },
    { value: 'Network', count: 31 },
  ],
};

function renderFilter(over: Record<string, unknown> = {}) {
  const props = {
    field: 'static:Category',
    dataType: 'string',
    selectedValues: [] as string[],
    onApply: vi.fn(),
    onClear: vi.fn(),
    isOpen: true,
    onToggle: vi.fn(),
    ...over,
  };
  render(<ColumnFilter {...props} />);
  return props;
}

beforeEach(() => {
  (assetsApi.getFieldValues as ReturnType<typeof vi.fn>).mockResolvedValue(VALUES);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ColumnFilter "Select all"', () => {
  it('selects every loaded value, then applies them', async () => {
    const onApply = vi.fn();
    renderFilter({ onApply });
    await screen.findByText('Workstation');

    fireEvent.click(screen.getByText('Select all'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    expect(onApply).toHaveBeenCalledWith('static:Category', [
      'Workstation',
      'Server',
      'Network',
    ]);
  });

  it('deselects all when toggled off from a fully-selected state', async () => {
    const onApply = vi.fn();
    renderFilter({ onApply, selectedValues: ['Workstation', 'Server', 'Network'] });
    await screen.findByText('Workstation');

    // All loaded values are selected, so "Select all" is checked → clicking clears them.
    fireEvent.click(screen.getByText('Select all'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    expect(onApply).toHaveBeenCalledWith('static:Category', []);
  });

  it('only selects values matching the active search', async () => {
    const onApply = vi.fn();
    renderFilter({ onApply });
    await screen.findByText('Workstation');

    // Narrow to just "Server", then Select all must apply only the filtered subset.
    fireEvent.change(screen.getByPlaceholderText('Search values...'), {
      target: { value: 'ser' },
    });
    fireEvent.click(screen.getByText('Select all'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    expect(onApply).toHaveBeenCalledWith('static:Category', ['Server']);
  });
});

describe('ColumnFilter dropdown placement', () => {
  it('portals the dropdown to document.body so ancestor transforms/backdrop-filters cannot displace it', async () => {
    renderFilter();
    const dropdown = (await screen.findByPlaceholderText('Search values...'))
      .parentElement as HTMLElement;

    // position:fixed resolves against the nearest transformed/backdrop-filtered
    // ancestor, not the viewport — inside a reporting glass card the dropdown
    // rendered mid-table. Portaling to <body> keeps the viewport as the
    // containing block wherever the filter is embedded.
    expect(dropdown.parentElement).toBe(document.body);
  });

  function renderInTh(rect: Partial<DOMRect>) {
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      top: 0,
      bottom: 0,
      left: 100,
      right: 260,
      width: 160,
      height: 20,
      x: 100,
      y: 0,
      toJSON: () => ({}),
      ...rect,
    } as DOMRect);
    Object.defineProperty(window, 'innerHeight', { value: 768, configurable: true });

    render(
      <table>
        <thead>
          <tr>
            <th>
              <ColumnFilter
                field="static:Category"
                dataType="string"
                selectedValues={[]}
                onApply={vi.fn()}
                onClear={vi.fn()}
                isOpen
                onToggle={vi.fn()}
              />
            </th>
          </tr>
        </thead>
      </table>,
    );
    const input = screen.getByPlaceholderText('Search values...');
    return input.parentElement as HTMLElement;
  }

  it('opens downward under the header when there is room below', () => {
    const dropdown = renderInTh({ top: 100, bottom: 120, y: 100 });
    expect(dropdown.style.top).toBe('124px');
    expect(dropdown.style.bottom).toBe('auto');
  });

  it('opens upward when the header sits near the bottom of the viewport', () => {
    // Header at y=700 in a 768px viewport: only 48px below — the 340px-tall
    // dropdown would run off-screen (the reported bug). It must flip above
    // the header instead.
    const dropdown = renderInTh({ top: 700, bottom: 720, y: 700 });
    expect(dropdown.style.bottom).toBe(`${768 - 700 + 4}px`);
    expect(dropdown.style.top).toBe('auto');
  });
});
