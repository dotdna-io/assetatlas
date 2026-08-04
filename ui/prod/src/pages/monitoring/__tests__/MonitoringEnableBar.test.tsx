// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock-prefixed so vitest hoists them alongside the vi.mock factories below.
const mockUseHealth = vi.fn();
const mockUpdateSetting = vi.fn();
const mockUseMonitoringStatus = vi.fn();

vi.mock('@/api/settings', () => ({
  useHealth: () => mockUseHealth(),
  settingsApi: { updateSetting: (...args: any[]) => mockUpdateSetting(...args) },
}));

vi.mock('@/api/monitoring', () => ({
  useMonitoringStatus: () => mockUseMonitoringStatus(),
}));

import { MonitoringEnableBar } from '../MonitoringEnableBar';

function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MonitoringEnableBar />
    </QueryClientProvider>,
  );
}

const healthWith = (monitoring: boolean) => ({
  data: { application: { features: { monitoring } } },
});

function stack(over: Partial<{ data: any; isLoading: boolean; isFetching: boolean }> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
    ...over,
  };
}

describe('MonitoringEnableBar', () => {
  beforeEach(() => {
    mockUseHealth.mockReset();
    mockUpdateSetting.mockReset().mockResolvedValue({});
    mockUseMonitoringStatus.mockReset().mockReturnValue(stack());
  });

  it('shows a loading placeholder (no checkbox) until health resolves', () => {
    mockUseHealth.mockReturnValue({ data: undefined });
    renderBar();
    expect(screen.getByText('Checking monitoring status…')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('reflects the effective enabled state from health', () => {
    mockUseHealth.mockReturnValue(healthWith(true));
    renderBar();
    expect(screen.getByRole('checkbox')).toBeChecked();
  });

  it('enabling (currently off) writes the "enabled" override', async () => {
    mockUseHealth.mockReturnValue(healthWith(false));
    renderBar();
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() =>
      expect(mockUpdateSetting).toHaveBeenCalledWith('monitoring', 'dashboards', {
        value: 'enabled',
      }),
    );
  });

  it('disabling (currently on) writes the "disabled" override', async () => {
    mockUseHealth.mockReturnValue(healthWith(true));
    renderBar();
    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() =>
      expect(mockUpdateSetting).toHaveBeenCalledWith('monitoring', 'dashboards', {
        value: 'disabled',
      }),
    );
  });

  it('shows the stack as Running when Prometheus is reachable', () => {
    mockUseHealth.mockReturnValue(healthWith(true));
    mockUseMonitoringStatus.mockReturnValue(stack({ data: { prometheus_reachable: true } }));
    renderBar();
    expect(screen.getByText('Running')).toBeInTheDocument();
  });

  it('shows the stack as stopped/not-deployed when Prometheus is unreachable', () => {
    mockUseHealth.mockReturnValue(healthWith(true));
    mockUseMonitoringStatus.mockReturnValue(stack({ data: { prometheus_reachable: false } }));
    renderBar();
    expect(screen.getByText('Stopped or not deployed')).toBeInTheDocument();
  });

  it('surfaces the host commands to start/stop the actual stack', () => {
    mockUseHealth.mockReturnValue(healthWith(true));
    renderBar();
    expect(screen.getByText(/assetatlas-cli enable monitoring/)).toBeInTheDocument();
    expect(screen.getByText(/assetatlas-cli disable monitoring/)).toBeInTheDocument();
  });

  it('Refresh re-runs the stack status probe', () => {
    mockUseHealth.mockReturnValue(healthWith(true));
    const s = stack();
    mockUseMonitoringStatus.mockReturnValue(s);
    renderBar();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(s.refetch).toHaveBeenCalled();
  });
});
