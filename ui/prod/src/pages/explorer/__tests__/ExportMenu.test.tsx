// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Explorer CSV export with an alert in the filter set.
 *
 * The alert used to ride its own request param; it is an ordinary entry in
 * filters[] now, so export inherits it wherever filters go and there is no
 * second channel for the two to disagree through.
 *
 * The one behaviour that CHANGED, deliberately (D7): "Export Full Inventory"
 * drops the alert, because it drops filters. #1011 kept the alert here when it
 * was an INVISIBLE scope the user could neither see nor remove — "full" could
 * not honestly mean "without the alert" then. It is a visible search-box token
 * now, so Full Inventory meaning the full inventory is exactly what the label
 * says, and the alert stops being the one filter Full does not drop.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { ExportMenu } from '../ExportMenu';
import { assetsApi } from '@/api/assets';

vi.mock('@/api/assets', () => ({
  assetsApi: { list: vi.fn(), aggregate: vi.fn() },
}));

const mockList = assetsApi.list as unknown as ReturnType<typeof vi.fn>;
const mockAggregate = assetsApi.aggregate as unknown as ReturnType<typeof vi.fn>;

// One Alert predicate, spelled as buildFilters() emits it.
const ALERT_FILTERS = JSON.stringify([
  { field_id: { kind: 'virtual', name: 'alert' }, op: 'eq', value: 'Asset With Public IP Address' },
]);
const COLUMNS = [{ key: 'static:Hostname', name: 'Hostname' }];

type MenuProps = Parameters<typeof ExportMenu>[0];

function renderMenu(props: Partial<MenuProps> = {}) {
  return render(
    <ExportMenu
      isOpen
      onClose={() => {}}
      totalFiltered={3}
      pivotMode={false}
      visibleColumns={COLUMNS}
      {...props}
    />,
  );
}

/** filters on every list() call so far (or the absence of the key). */
function filtersSent(): (string | undefined)[] {
  return mockList.mock.calls.map((c) => c[0]?.filters);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue({
    assets: [],
    pagination: { pages: 1, total: 0, page: 1, limit: 5000 },
  });
  mockAggregate.mockResolvedValue({ rows: [], columns: [], data: {} });
  // jsdom implements neither; downloadCSV uses both after a successful fetch.
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => 'blob:x');
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
});

describe('ExportMenu with an alert filter', () => {
  it('carries the alert on every page of a Filtered export', async () => {
    // Two pages so the loop call site is covered too — carrying it only on the
    // first request would still widen the CSV from page 2 on.
    mockList
      .mockResolvedValueOnce({ assets: [], pagination: { pages: 2, total: 6, page: 1, limit: 5000 } })
      .mockResolvedValueOnce({ assets: [], pagination: { pages: 2, total: 6, page: 2, limit: 5000 } });
    const user = userEvent.setup();
    renderMenu({ filters: ALERT_FILTERS });

    await user.click(screen.getByText(/Export Filtered Data/));

    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
    expect(filtersSent()).toEqual([ALERT_FILTERS, ALERT_FILTERS]);
  });

  it('drops the alert on a Full export, exactly as it drops every other filter', async () => {
    const user = userEvent.setup();
    renderMenu({ filters: ALERT_FILTERS });

    await user.click(screen.getByText(/Export Full Inventory/));

    await waitFor(() => expect(mockList).toHaveBeenCalled());
    expect(mockList.mock.calls[0][0]).not.toHaveProperty('filters');
  });

  it('never sends an alert_id parameter', async () => {
    const user = userEvent.setup();
    renderMenu({ filters: ALERT_FILTERS });

    await user.click(screen.getByText(/Export Filtered Data/));

    await waitFor(() => expect(mockList).toHaveBeenCalled());
    expect(mockList.mock.calls[0][0]).not.toHaveProperty('alert_id');
  });

  it('narrows a Pivot export to the alert through filters', async () => {
    // #1013's behaviour, preserved for free: the pivot export always passes
    // filters, so it inherits the alert without an alert_id of its own.
    const user = userEvent.setup();
    renderMenu({
      pivotMode: true,
      filters: ALERT_FILTERS,
      pivotConfig: { rowDimension: 'category', columnDimension: '', columnsEnabled: false },
    });

    const pivotItem = screen.getByText('Export Pivot Table');
    expect(pivotItem).not.toHaveAttribute('aria-disabled', 'true');

    await user.click(pivotItem);
    await waitFor(() => expect(mockAggregate).toHaveBeenCalled());
    const params = mockAggregate.mock.calls[0][0];
    expect(params.filters).toBe(ALERT_FILTERS);
    expect(params).not.toHaveProperty('alert_id');
  });
});
