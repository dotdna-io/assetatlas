// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockUseHealth = vi.fn();
const mockUpdateSetting = vi.fn();
const mockUseMonitoringStorage = vi.fn();

vi.mock('@/api/settings', () => ({
  useHealth: () => mockUseHealth(),
  settingsApi: { updateSetting: (...args: any[]) => mockUpdateSetting(...args) },
}));

vi.mock('@/api/monitoring', () => ({
  useMonitoringStorage: () => mockUseMonitoringStorage(),
}));

import { MonitoringRetentionPanel } from '../MonitoringRetentionPanel';

function renderPanel(qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <MonitoringRetentionPanel />
    </QueryClientProvider>,
  );
}

function storage(over: Partial<any> = {}) {
  return {
    data: {
      reachable: true,
      detail: null,
      applied_retention: '15d',
      applied_retention_seconds: 1296000,
      desired_retention: '',
      pending: false,
      disk_used_bytes: 1200000000,
      head_series: 21456,
      samples_per_second: 3100,
      history_seconds: 432000, // 5 days: keeps the projected size (~3.6 GB) distinct from disk used (1.2 GB)
    },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
    ...over,
  };
}

describe('MonitoringRetentionPanel', () => {
  beforeEach(() => {
    mockUseHealth.mockReset().mockReturnValue({ data: { application: { features: { monitoring: true } } } });
    mockUpdateSetting.mockReset().mockResolvedValue({});
    mockUseMonitoringStorage.mockReset().mockReturnValue(storage());
  });

  it('renders storage numbers when reachable', () => {
    renderPanel();
    expect(screen.getByText(/1\.2 GB/)).toBeInTheDocument();     // disk used
    expect(screen.getByText(/21,456/)).toBeInTheDocument();      // head series
  });

  it('shows a loading note (not the unreachable note) while the query is in flight', () => {
    mockUseMonitoringStorage.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: true,
      refetch: vi.fn(),
    });
    renderPanel();
    expect(screen.getByText(/checking monitoring storage/i)).toBeInTheDocument();
    expect(screen.queryByText(/not reachable/i)).not.toBeInTheDocument();
  });

  it('shows an unreachable note instead of numbers when the stack is down', () => {
    mockUseMonitoringStorage.mockReturnValue(
      storage({ data: { ...storage().data, reachable: false, disk_used_bytes: null } }),
    );
    renderPanel();
    expect(screen.getByText(/not reachable/i)).toBeInTheDocument();
  });

  it('writes the chosen retention to the setting and invalidates the storage query', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    renderPanel(qc);
    fireEvent.change(screen.getByLabelText('Desired retention'), { target: { value: '30d' } });
    await waitFor(() =>
      expect(mockUpdateSetting).toHaveBeenCalledWith('monitoring', 'retention', { value: '30d' }),
    );
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['monitoring', 'storage'], exact: true }),
    );
  });

  it('shows the pending badge + host command when pending', () => {
    mockUseMonitoringStorage.mockReturnValue(
      storage({ data: { ...storage().data, desired_retention: '30d', pending: true } }),
    );
    renderPanel();
    expect(screen.getByText(/pending/i)).toBeInTheDocument();
    expect(screen.getByText(/assetatlas-cli apply-retention/)).toBeInTheDocument();
  });

  it('does not show the pending badge when not pending', () => {
    renderPanel();
    expect(screen.queryByText(/pending/i)).not.toBeInTheDocument();
  });
});
