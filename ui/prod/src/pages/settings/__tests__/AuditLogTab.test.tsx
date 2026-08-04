// SPDX-License-Identifier: AGPL-3.0-or-later
// Tests for the AuditLogTab semantic columns + filters (#1150, slice 4).
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { AuditLogTab } from '../AuditLogTab';
import { useAuditLog } from '@/api/settings';
import type { AuditLogEntry } from '@/api/settings';

vi.mock('@/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/settings')>();
  return { ...actual, useAuditLog: vi.fn() };
});

const mockUseAuditLog = useAuditLog as ReturnType<typeof vi.fn>;

const ANNOTATED: AuditLogEntry = {
  id: '1',
  timestamp: '2026-07-30T09:00:00Z',
  username: 'alice',
  method: 'PUT',
  path: '/api/integrations/sentinelone',
  status_code: 200,
  ip_address: '127.0.0.1',
  duration_ms: 12,
  category: 'config',
  resource_type: 'integration',
  resource_id: 'sentinelone',
  action: 'update',
  details: { field: 'name', old: 'S1', new: 'SentinelOne' },
};

// HTTP-only row: nothing annotated the request, so all semantic fields NULL.
const HTTP_ONLY: AuditLogEntry = {
  id: '2',
  timestamp: '2026-07-30T09:01:00Z',
  username: 'alice',
  method: 'GET',
  path: '/api/assets',
  status_code: 200,
  ip_address: '127.0.0.1',
  duration_ms: 4,
  category: undefined,
  resource_type: undefined,
  resource_id: undefined,
  action: undefined,
  details: undefined,
};

function mockData(entries: AuditLogEntry[]) {
  mockUseAuditLog.mockReturnValue({
    data: { entries, total: entries.length, page: 1, pages: 1, per_page: 50 },
    isLoading: false,
  });
}

function renderTab(initialUrl = '/settings?tab=audit') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <AuditLogTab />
    </MemoryRouter>,
  );
}

function lastCallParams(): Record<string, unknown> {
  const calls = mockUseAuditLog.mock.calls;
  return calls[calls.length - 1][0];
}

describe('AuditLogTab semantic columns', () => {
  beforeEach(() => {
    mockUseAuditLog.mockReset();
    mockData([ANNOTATED]);
  });

  it('renders the Category, Action and Resource column headers', () => {
    renderTab();
    expect(screen.getByRole('columnheader', { name: 'Category' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Action' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Resource' })).toBeInTheDocument();
  });

  it('renders semantic values for an annotated row', () => {
    renderTab();
    expect(screen.getByText('config')).toBeInTheDocument();
    expect(screen.getByText('update')).toBeInTheDocument();
    // Resource = resource_type:resource_id when both are present.
    expect(screen.getByText('integration:sentinelone')).toBeInTheDocument();
  });

  it('renders resource_type alone when resource_id is NULL', () => {
    mockData([{ ...ANNOTATED, resource_id: undefined }]);
    renderTab();
    const cell = screen.getByText('integration');
    expect(cell).toBeInTheDocument();
  });

  it('renders an em-dash for every NULL semantic field (HTTP-only row)', () => {
    mockData([HTTP_ONLY]);
    renderTab();
    // Category, Action and Resource cells all fall back to the em-dash.
    expect(screen.getAllByText('—')).toHaveLength(3);
  });

  it('renders the annotation details section for an annotated row', () => {
    // The detail row is always in the DOM (collapsed via CSS); the Details
    // section inside it renders only when the entry carries details.
    renderTab();
    expect(screen.getByText('Details')).toBeInTheDocument();
    expect(screen.getByText(/"field": "name"/)).toBeInTheDocument();
  });

  it('omits the Details section for an HTTP-only row', () => {
    mockData([HTTP_ONLY]);
    renderTab();
    fireEvent.click(screen.getByText('/api/assets'));
    expect(screen.queryByText('Details')).not.toBeInTheDocument();
  });
});

describe('AuditLogTab semantic filters', () => {
  beforeEach(() => {
    mockUseAuditLog.mockReset();
    mockData([ANNOTATED]);
  });

  it('sends the four semantic params after typing + Search', async () => {
    renderTab();
    fireEvent.change(screen.getByPlaceholderText('e.g. config'), {
      target: { value: 'config' },
    });
    fireEvent.change(screen.getByPlaceholderText('e.g. update'), {
      target: { value: 'update' },
    });
    fireEvent.change(screen.getByPlaceholderText('e.g. integration'), {
      target: { value: 'integration' },
    });
    fireEvent.change(screen.getByPlaceholderText('e.g. sentinelone'), {
      target: { value: 'sentinelone' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      const params = lastCallParams();
      expect(params.category).toBe('config');
      expect(params.action).toBe('update');
      expect(params.resource_type).toBe('integration');
      expect(params.resource_id).toBe('sentinelone');
    });
  });

  it('applies a semantic filter on Enter, like the other text filters', async () => {
    renderTab();
    const input = screen.getByPlaceholderText('e.g. update');
    fireEvent.change(input, { target: { value: 'delete' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(lastCallParams().action).toBe('delete');
    });
  });

  it('keeps the existing filters working alongside the new ones', async () => {
    renderTab();
    fireEvent.change(screen.getByPlaceholderText('e.g. /api/assets'), {
      target: { value: '/api/integrations' },
    });
    fireEvent.change(screen.getByPlaceholderText('e.g. config'), {
      target: { value: 'admin' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      const params = lastCallParams();
      expect(params.path).toBe('/api/integrations');
      expect(params.category).toBe('admin');
    });
  });

  it('initialises filters from the URL and clears empty params', async () => {
    renderTab('/settings?tab=audit&category=security&action=access_denied');
    await waitFor(() => {
      const params = lastCallParams();
      expect(params.category).toBe('security');
      expect(params.action).toBe('access_denied');
      expect(params.resource_type).toBeUndefined();
      expect(params.resource_id).toBeUndefined();
    });
  });
});
