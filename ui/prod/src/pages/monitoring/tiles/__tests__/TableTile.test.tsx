// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

// Resolved instant-query so the Tile renders its children (the table) rather
// than the loading/empty/error state.
vi.mock('@/api/monitoring', () => ({
  useInstantQuery: () => ({
    data: {
      resultType: 'vector',
      result: [
        { metric: { endpoint: '/api/x' }, value: [0, '5'] },
        { metric: { endpoint: '/api/y' }, value: [0, '2'] },
      ],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

import { TableTile } from '../TableTile';

describe('TableTile', () => {
  it('renders a compact table with the endpoint rows and formatted values', () => {
    // Regression: monitoring tables used the heavy interactive DotDNADataTable
    // (search / pagination / column-picker) crammed into a 220px tile. They are
    // now a plain presentational table — assert the rows render with the value
    // column formatted, and no data-explorer chrome (search box) is present.
    render(
      <TableTile
        title="Top endpoints"
        query="topk(10, x)"
        refetchInterval={false}
        columns={[{ label: 'Endpoint', key: 'endpoint' }]}
        formatValue={(v) => `${v.toFixed(0)} ms`}
      />,
    );

    expect(screen.getByText('/api/x')).toBeInTheDocument();
    expect(screen.getByText('/api/y')).toBeInTheDocument();
    expect(screen.getByText('5 ms')).toBeInTheDocument();
    expect(screen.getByText('2 ms')).toBeInTheDocument();
    // Column headers (the label column + the appended Value column).
    expect(screen.getByRole('columnheader', { name: 'Endpoint' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Value' })).toBeInTheDocument();
    // No interactive data-explorer chrome.
    expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
  });
});
