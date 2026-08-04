// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import type { Alert } from '@/api/alerts';

vi.mock('@/api/alerts', async () => {
  const actual = await vi.importActual<typeof import('@/api/alerts')>('@/api/alerts');
  return {
    ...actual,
    useAlerts: vi.fn(),
  };
});

import { useAlerts } from '@/api/alerts';
import AlertsPage from '../../AlertsPage';

// Relative to "now" so this suite never bit-rots against the default 7d time-range filter.
const RECENT = new Date(Date.now() - 3600_000).toISOString();

const baseAlert: Alert = {
  id: 'a-1',
  type: 'operational',
  severity: 'high',
  status: 'active',
  title: 'Base alert',
  description: null,
  reason: null,
  source: 'system',
  asset_count: 0,
  assets: [],
  assets_by_category: null,
  data_sources: [],
  subject_type: 'asset',
  subjects: null,
  threshold: null,
  created_at: RECENT,
  updated_at: RECENT,
  resolved_at: null,
};

function renderWithAlert(alert: Alert) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  (useAlerts as any).mockReturnValue({
    data: {
      alerts: [alert],
      summary: { total: 1, by_severity: {}, by_type: {}, by_status: {} },
      timeline: [],
      pagination: { page: 1, per_page: 20, total_items: 1, total_pages: 1 },
    },
    isLoading: false,
    error: null,
  });
  render(
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AlertsPage />
      </BrowserRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText(alert.title));
}

describe('AlertsPage — Reason section de-dup', () => {
  it('does not render the Reason section when reason equals description', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Duplicate reason alert',
      description: 'Same text either way.',
      reason: 'Same text either way.',
    });

    // Description still renders (and is the ONLY place this text appears —
    // getByText would throw on a duplicate match if Reason also rendered it).
    expect(screen.getByText('Same text either way.')).toBeInTheDocument();
    expect(screen.queryByText('Reason')).not.toBeInTheDocument();
  });

  it('renders the Reason section when reason differs from description', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'Distinct reason alert',
      description: 'What happened.',
      reason: 'Why it happened.',
    });

    expect(screen.getByText('Description')).toBeInTheDocument();
    expect(screen.getByText('What happened.')).toBeInTheDocument();
    expect(screen.getByText('Reason')).toBeInTheDocument();
    expect(screen.getByText('Why it happened.')).toBeInTheDocument();
  });

  it('does not render the Reason section, or a placeholder, when reason is null', () => {
    renderWithAlert({
      ...baseAlert,
      title: 'No reason alert',
      description: 'Some description.',
      reason: null,
    });

    expect(screen.queryByText('Reason')).not.toBeInTheDocument();
    expect(screen.queryByText('No reason provided')).not.toBeInTheDocument();
  });
});
