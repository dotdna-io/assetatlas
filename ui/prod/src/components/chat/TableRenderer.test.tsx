// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TableRenderer } from './TableRenderer';
import type { TableArtifact } from './chartArtifactTypes';

vi.mock('@/api/assets', () => ({
  assetsApi: {
    exportAssetsCsv: vi.fn(() =>
      Promise.resolve({ blob: new Blob(['x']), filename: 'f.csv' })),
    exportAggregateCsv: vi.fn(() =>
      Promise.resolve({ blob: new Blob(['x']), filename: 'agg.csv' })),
  },
}));
vi.mock('@/utils/clipboard', () => ({ downloadBlob: vi.fn() }));

import { assetsApi } from '@/api/assets';
import { downloadBlob } from '@/utils/clipboard';

const listArtifact = (
  query?: TableArtifact['meta']['query'],
  source_tool: TableArtifact['meta']['source_tool'] = 'list_assets',
): TableArtifact => ({
  type: 'table',
  title: 'Assets (29 total, showing 29)',
  columns: [{ key: 'hostname', label: 'Hostname' }],
  series: [{ hostname: 'web01' }],
  meta: { source_tool, layout: 'list', total: 29, query },
});

const aggregateArtifact = (
  query?: TableArtifact['meta']['query'],
): TableArtifact => ({
  type: 'table',
  title: 'Assets by Category',
  columns: [{ key: 'Category', label: 'Category' }, { key: 'count', label: 'Count' }],
  series: [{ Category: 'server', count: 3 }],
  meta: { source_tool: 'export_assets', layout: 'aggregate', total: 3, query },
});

describe('TableRenderer Download CSV', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the button for a list_assets table with a query', () => {
    render(<TableRenderer artifact={listArtifact({ search: 'ubuntu' })} />);
    // getByRole throws if absent; matcher-free so no jest-dom dependency.
    expect(screen.getByRole('button', { name: /download csv/i })).toBeTruthy();
  });

  it('shows the button for query_assets and export_assets list tables', () => {
    for (const tool of ['query_assets', 'export_assets'] as const) {
      const { unmount } = render(
        <TableRenderer artifact={listArtifact({ search: 'x' }, tool)} />,
      );
      expect(screen.getByRole('button', { name: /download csv/i })).toBeTruthy();
      unmount();
    }
  });

  it('hides the button when there is no query', () => {
    render(<TableRenderer artifact={listArtifact(undefined)} />);
    expect(screen.queryByRole('button', { name: /download csv/i })).toBeNull();
  });

  it('hides the button for search_assets tables (different query shape)', () => {
    render(<TableRenderer artifact={listArtifact({ search: 'x' }, 'search_assets')} />);
    expect(screen.queryByRole('button', { name: /download csv/i })).toBeNull();
  });

  it('downloads a list on click via exportAssetsCsv with the artifact query', async () => {
    const query = { filters: [{ field: 'OS', op: 'eq', value: 'Ubuntu' }], fields: 'static:Hostname' };
    render(<TableRenderer artifact={listArtifact(query)} />);
    fireEvent.click(screen.getByRole('button', { name: /download csv/i }));
    await waitFor(() => expect(assetsApi.exportAssetsCsv).toHaveBeenCalledWith(query));
    await waitFor(() => expect(downloadBlob).toHaveBeenCalled());
  });

  it('downloads an aggregate matrix via exportAggregateCsv', async () => {
    const query = { row_field: 'Category', agg: 'COUNT' };
    render(<TableRenderer artifact={aggregateArtifact(query)} />);
    fireEvent.click(screen.getByRole('button', { name: /download csv/i }));
    await waitFor(() => expect(assetsApi.exportAggregateCsv).toHaveBeenCalledWith(query));
    // and it must NOT hit the list export path
    expect(assetsApi.exportAssetsCsv).not.toHaveBeenCalled();
  });
});
