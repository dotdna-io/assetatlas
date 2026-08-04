// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { ChartEditor } from '../ChartEditor';
import { DEFAULT_COLUMNS } from '../../grid/ReportTable';

// ReportTable fetches assets; stub it so the editor renders without a network.
// The stub surfaces the props it receives as data-attributes so the test can
// verify the editor forwards the real columns/filters/itemsPerPage values —
// a plain "renders" stub would stay green even if a prop name were typo'd.
// Keep the module's other exports (ChartEditor imports DEFAULT_COLUMNS).
vi.mock('../../grid/ReportTable', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ReportTable: ({ columns, filters, itemsPerPage }: any) => (
    <div
      data-testid="table-preview"
      data-columns={JSON.stringify(columns ?? null)}
      data-filters={JSON.stringify(filters ?? null)}
      data-items-per-page={itemsPerPage}
    />
  ),
}));

const tableChart = {
  id: 1, chartId: 'e0a1b2c3-4d5e-6f7a-8b9c-0d1e2f3a4b5c',
  className: 'DotDNADataTable', name: 'Data Table', size: '6x4',
  allowedSizes: ['6x4'], gridRow: 1, gridColumn: 1, defaultOptions: {},
};

afterEach(() => {
  vi.useRealTimers();
});

describe('ChartEditor — Data Table preview', () => {
  it('renders a live preview scoped to the configured columns/filters/itemsPerPage', () => {
    vi.useFakeTimers();
    render(
      <ChartEditor isOpen chart={tableChart as any} assetFields={[]}
        onClose={() => {}} onSave={() => {}} />,
    );
    expect(screen.getByTestId('table-preview')).toBeInTheDocument();

    // tableChart has no saved queryConfig, so the editor seeds answers with
    // the implicit DEFAULT_COLUMNS set (what ReportTable shows when nothing
    // is picked) plus the 'table' template's field defaults: no filters,
    // itemsPerPage 25. The preview is fed those values after the 500ms
    // debounce, matching what Save would generate.
    act(() => {
      vi.advanceTimersByTime(500);
    });
    const preview = screen.getByTestId('table-preview');
    expect(preview).toHaveAttribute('data-columns', JSON.stringify(DEFAULT_COLUMNS));
    expect(preview).toHaveAttribute('data-filters', '[]');
    expect(preview).toHaveAttribute('data-items-per-page', '25');
  });
});
