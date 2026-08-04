// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TableRenderer } from '../TableRenderer';
import { makeTableArtifact } from './fixtures';


describe('TableRenderer · keyvalue layout', () => {
  it('renders one row per non-empty asset field', () => {
    const a = makeTableArtifact({
      title: 'Asset abc',
      meta: { layout: 'keyvalue', source_tool: 'get_asset',
              asset_id: 'abc' },
      columns: [
        { key: 'field', label: 'Field' },
        { key: 'value', label: 'Value' },
      ],
      series: [
        { field: 'Hostname', value: ['host1.example'] },
        { field: 'Category', value: 'server' },
      ],
    });
    render(<TableRenderer artifact={a} />);
    expect(screen.getByText('Hostname')).toBeInTheDocument();
    expect(screen.getByText('host1.example')).toBeInTheDocument();
    expect(screen.getByText('Category')).toBeInTheDocument();
    // Category cell renders as a colored Badge using shared palette.
    const cell = screen.getByText('server');
    // Badge renders as a span with inline-style; jsdom normalizes hex to
    // rgb(), so check for rgb(59, 130, 246) — the rgb equivalent of #3b82f6.
    let el: HTMLElement | null = cell;
    let foundColor = false;
    while (el) {
      const style = el.getAttribute('style') ?? '';
      if (style.includes('59, 130, 246')) { foundColor = true; break; }
      el = el.parentElement;
    }
    expect(foundColor).toBe(true);
  });

  it('renders the title', () => {
    const a = makeTableArtifact({
      title: 'Asset xyz',
      meta: { layout: 'keyvalue', source_tool: 'get_asset',
              asset_id: 'xyz' },
    });
    render(<TableRenderer artifact={a} />);
    expect(screen.getByText('Asset xyz')).toBeInTheDocument();
  });
});

describe('TableRenderer · list layout', () => {
  it('renders a header per column and a row per asset', () => {
    const a = makeTableArtifact({
      title: 'Assets (2)',
      meta: { layout: 'list', source_tool: 'list_assets',
              total: 2, truncated: false },
      columns: [
        { key: 'hostname', label: 'Hostname' },
        { key: 'category', label: 'Category' },
      ],
      series: [
        { hostname: ['h1'], category: 'server' },
        { hostname: ['h2'], category: 'workstation' },
      ],
    });
    render(<TableRenderer artifact={a} />);
    expect(screen.getByRole('columnheader', { name: 'Hostname' }))
      .toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Category' }))
      .toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(3); // header + 2 rows
  });

  it('shows truncation footnote when meta.truncated is true', () => {
    const a = makeTableArtifact({
      title: 'Assets (50 total, showing 25)',
      meta: { layout: 'list', source_tool: 'list_assets',
              total: 50, truncated: true },
      columns: [{ key: 'hostname', label: 'Hostname' }],
      series: Array.from({ length: 25 },
        (_, i) => ({ hostname: [`h${i}`] })),
    });
    render(<TableRenderer artifact={a} />);
    expect(screen.getByText(/Showing first 25 of 50/))
      .toBeInTheDocument();
  });

  it('renders multi-value array cells as comma-joined values', () => {
    const a = makeTableArtifact({
      title: 'Assets (1)',
      meta: { layout: 'list', source_tool: 'list_assets',
              total: 1, truncated: false },
      columns: [{ key: 'hostname', label: 'Hostname' }],
      series: [{ hostname: ['h1.example', 'h1-alt.example'] }],
    });
    render(<TableRenderer artifact={a} />);
    // Rendered as comma-separated tokens in one cell.
    const cell = screen.getByText(/h1\.example/);
    // The cell text contains both hostnames joined.
    expect(cell.textContent).toMatch(/h1\.example/);
    expect(cell.textContent).toMatch(/h1-alt\.example/);
  });

  it('does not show footnote when not truncated', () => {
    const a = makeTableArtifact({
      meta: { layout: 'list', source_tool: 'list_assets',
              total: 2, truncated: false },
    });
    render(<TableRenderer artifact={a} />);
    expect(screen.queryByText(/Showing first/)).toBeNull();
  });
});
