// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ExplorerTable } from '../ExplorerTable';

// Minimal prop builder — ExplorerTable renders the `columns` prop directly, so
// the add-to-search wiring can be exercised with a couple of columns and one
// asset. Everything not relevant to the menu is a no-op default.
function renderTable(over: Record<string, unknown> = {}) {
  const columns = [
    { key: 'static:Category', name: 'Category', dataType: 'string', sortable: true },
    { key: 'static:Model', name: 'Model', dataType: 'string_array', sortable: true },
    { key: 'static:Last%20Seen', name: 'Last Seen', dataType: 'timestamp', sortable: true },
  ];
  const props = {
    assets: [
      {
        id: 'a1',
        'static:Category': 'Workstation',
        'static:Model': ['OptiPlex', 'Latitude'],
        'static:Last%20Seen': '2026-06-01T00:00:00Z',
      },
    ],
    columns,
    sortColumn: '',
    sortDirection: 'asc' as const,
    onSort: vi.fn(),
    onEditAsset: vi.fn(),
    isLoading: false,
    columnOrder: columns.map((c) => c.key),
    onColumnReorder: vi.fn(),
    columnWidths: {},
    onColumnResize: vi.fn(),
    columnFilters: {},
    dateFilters: {},
    onColumnFilter: vi.fn(),
    onClearColumnFilter: vi.fn(),
    onAddToSearch: vi.fn(),
    ...over,
  };
  render(<ExplorerTable {...props} />);
  return props;
}

describe('ExplorerTable add-to-search context menu', () => {
  it('offers Add and Exclude on right-click of a scalar cell, with the right previews', () => {
    renderTable();
    fireEvent.contextMenu(screen.getByText('Workstation'));

    expect(screen.getByRole('menuitem', { name: /Add to search/ })).toHaveTextContent(
      'Category = Workstation',
    );
    expect(screen.getByRole('menuitem', { name: /Exclude from search/ })).toHaveTextContent(
      'Category != Workstation',
    );
  });

  it('Add to search reports the value with the = operator', () => {
    const onAddToSearch = vi.fn();
    renderTable({ onAddToSearch });
    fireEvent.contextMenu(screen.getByText('Workstation'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Add to search/ }));
    expect(onAddToSearch).toHaveBeenCalledWith('Category', 'Workstation', '=');
  });

  it('Exclude from search reports the value with the != operator', () => {
    const onAddToSearch = vi.fn();
    renderTable({ onAddToSearch });
    fireEvent.contextMenu(screen.getByText('Workstation'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Exclude from search/ }));
    expect(onAddToSearch).toHaveBeenCalledWith('Category', 'Workstation', '!=');
  });

  it('targets the specific clicked item in a multi-value cell', () => {
    const onAddToSearch = vi.fn();
    renderTable({ onAddToSearch });

    // Right-click the second value, not the first — the menu must carry it.
    fireEvent.contextMenu(screen.getByText('Latitude'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Add to search/ }));
    expect(onAddToSearch).toHaveBeenCalledWith('Model', 'Latitude', '=');
  });

  it('does not open for an ineligible column (date)', () => {
    renderTable();
    fireEvent.contextMenu(screen.getByText('2026-06-01'));
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
  });
});
