// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ASSET_LABEL_COLUMN_KEY } from '@/constants/assetTableColumns';
import { ExplorerTable } from '../ExplorerTable';

// The pinned Asset Label column is locked first: it must not be draggable,
// and no drop may insert another column ahead of it (#1153). The guards live
// in ExplorerTable itself so every page rendering the table inherits them.
function renderTable(over: Record<string, unknown> = {}) {
  const columns = [
    { key: ASSET_LABEL_COLUMN_KEY, name: 'Asset Label', dataType: 'string', sortable: true },
    { key: 'static:Hostname', name: 'Hostname', dataType: 'string_array', sortable: true },
    { key: 'static:Category', name: 'Category', dataType: 'string', sortable: true },
  ];
  const props = {
    assets: [
      {
        id: 'a1',
        [ASSET_LABEL_COLUMN_KEY]: 'switch-01',
        'static:Hostname': ['switch-01'],
        'static:Category': 'Network',
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
    ...over,
  };
  render(<ExplorerTable {...props} />);
  return props;
}

function headerCell(name: string): HTMLTableCellElement {
  const th = screen.getByText(name).closest('th');
  if (!th) throw new Error(`no <th> found for ${name}`);
  return th;
}

describe('ExplorerTable pinned Asset Label column', () => {
  it('renders the pinned column undraggable; other columns stay draggable', () => {
    renderTable();
    expect(headerCell('Asset Label')).toHaveAttribute('draggable', 'false');
    expect(headerCell('Hostname')).toHaveAttribute('draggable', 'true');
    expect(headerCell('Category')).toHaveAttribute('draggable', 'true');
  });

  it('rejects a drop onto the pinned column (no insert before it)', () => {
    const onColumnReorder = vi.fn();
    renderTable({ onColumnReorder });
    const dataTransfer = { effectAllowed: '', dropEffect: '' };

    fireEvent.dragStart(headerCell('Category'), { dataTransfer });
    fireEvent.drop(headerCell('Asset Label'), { dataTransfer });
    fireEvent.dragEnd(headerCell('Category'), { dataTransfer });

    expect(onColumnReorder).not.toHaveBeenCalled();
  });

  it('ignores a drag started on the pinned column itself', () => {
    const onColumnReorder = vi.fn();
    renderTable({ onColumnReorder });
    const dataTransfer = { effectAllowed: '', dropEffect: '' };

    // fireEvent dispatches even though draggable=false — the handler's own
    // guard is what must refuse the drag.
    fireEvent.dragStart(headerCell('Asset Label'), { dataTransfer });
    fireEvent.drop(headerCell('Category'), { dataTransfer });

    expect(onColumnReorder).not.toHaveBeenCalled();
  });

  it('still reorders between unpinned columns', () => {
    const onColumnReorder = vi.fn();
    renderTable({ onColumnReorder });
    const dataTransfer = { effectAllowed: '', dropEffect: '' };

    // Dropping Category onto Hostname inserts before Hostname; the pin is
    // untouched at the front.
    fireEvent.dragStart(headerCell('Category'), { dataTransfer });
    fireEvent.drop(headerCell('Hostname'), { dataTransfer });

    expect(onColumnReorder).toHaveBeenCalledWith([
      ASSET_LABEL_COLUMN_KEY,
      'static:Category',
      'static:Hostname',
    ]);
  });
});
