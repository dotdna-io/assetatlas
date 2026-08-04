// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

import { AssistantActions, buildExplorerUrl } from '../AssistantActions';

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('buildExplorerUrl', () => {
  it('returns bare /explorer when no args', () => {
    expect(buildExplorerUrl(undefined)).toBe('/explorer');
    expect(buildExplorerUrl({})).toBe('/explorer');
  });

  it('passes `search` verbatim', () => {
    const url = buildExplorerUrl({ search: 'hostname=foo' });
    expect(url).toBe('/explorer?search=hostname%3Dfoo');
  });

  it('maps eq filters into ?filters column-filter JSON', () => {
    const url = buildExplorerUrl({
      filters: [
        { field: 'category', op: 'eq', value: 'Server' },
        { field: 'category', op: 'eq', value: 'Workstation' },
      ],
    });
    const qs = new URLSearchParams(url.split('?')[1] || '');
    const filters = JSON.parse(qs.get('filters') || '{}');
    expect(filters).toEqual({ category: ['Server', 'Workstation'] });
  });

  it('accepts filters passed as a JSON string', () => {
    const url = buildExplorerUrl({
      filters: JSON.stringify([{ field: 'site', op: 'eq', value: 'HQ' }]),
    });
    const qs = new URLSearchParams(url.split('?')[1] || '');
    expect(JSON.parse(qs.get('filters') || '{}')).toEqual({ site: ['HQ'] });
  });

  it('ignores non-eq filters (they cannot round-trip into column filters)', () => {
    const url = buildExplorerUrl({
      filters: [{ field: 'hostname', op: 'contains', value: 'web' }],
      search: 'web',
    });
    expect(url).toContain('search=web');
    expect(url).not.toContain('filters=');
  });
});

describe('AssistantActions', () => {
  const baseProps = { messageId: 'm1' };

  it('renders nothing when no explorable tool call', () => {
    const { container } = render(
      <AssistantActions {...baseProps} toolCalls={[]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when tool call list is missing entirely', () => {
    const { container } = render(<AssistantActions {...baseProps} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows Open in Explorer for list_assets and opens a new tab', () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);
    render(
      <AssistantActions
        {...baseProps}
        toolCalls={[
          {
            id: 't1',
            name: 'list_assets',
            arguments: {
              search: 'foo',
              filters: [{ field: 'category', op: 'eq', value: 'Server' }],
            },
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByText(/open in explorer/i));
    expect(openSpy).toHaveBeenCalled();
    const url = openSpy.mock.calls[0][0] as string;
    expect(url).toMatch(/^\/explorer\?/);
    expect(url).toContain('search=foo');
    expect(url).toContain('filters=');
  });

  it('renders Regenerate when onRegenerate is provided', () => {
    const onRegenerate = vi.fn();
    render(
      <AssistantActions
        {...baseProps}
        onRegenerate={onRegenerate}
      />,
    );
    const btn = screen.getByRole('button', { name: /regenerate/i });
    fireEvent.click(btn);
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it('omits Regenerate when onRegenerate is not provided', () => {
    render(<AssistantActions {...baseProps} />);
    expect(screen.queryByRole('button', { name: /regenerate/i })).not.toBeInTheDocument();
  });

  it('uses the first aggregate_assets call as explorable', () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);
    render(
      <AssistantActions
        {...baseProps}
        toolCalls={[
          { id: 't0', name: 'get_dashboard', arguments: {} },
          { id: 't1', name: 'aggregate_assets',
            arguments: { search: 'count' } },
        ]}
      />,
    );
    expect(screen.getByText(/open in explorer/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/open in explorer/i));
    expect(openSpy.mock.calls[0][0]).toContain('search=count');
  });
});
