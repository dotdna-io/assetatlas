// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { HealthCard } from '@/pages/integration-details/HealthCard';
import { useIntegrationHealth } from '@/api/integrations';
import type { Integration } from '@/api/integrations';

vi.mock('@/api/integrations');

const integration = {
  id: '11111111-1111-1111-1111-111111111111',
  last_connection_test_status: 'success',
} as unknown as Integration;

function mockHealth(status: string | null) {
  (useIntegrationHealth as any) = vi.fn().mockReturnValue({
    data: status == null
      ? undefined
      : { success: true, status, last_successful_run: null, last_error: null, is_running: false },
  });
}

function statusDot(container: HTMLElement): HTMLElement {
  const dot = container.querySelector('span[class*="statusDot"]');
  expect(dot).not.toBeNull();
  return dot as HTMLElement;
}

describe('HealthCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the warning state distinctly for a partial sync (#1334)', () => {
    mockHealth('warning');
    const { container } = render(
      <HealthCard integration={integration} refetchInterval={0} />,
    );

    expect(screen.getByText('Warning')).toBeInTheDocument();
    const dot = statusDot(container);
    expect(dot.className).toMatch(/warning/);
    expect(dot.className).not.toMatch(/unknown/);
  });

  it('keeps the healthy state for a clean sync', () => {
    mockHealth('healthy');
    const { container } = render(
      <HealthCard integration={integration} refetchInterval={0} />,
    );

    expect(screen.getByText('Healthy')).toBeInTheDocument();
    expect(statusDot(container).className).toMatch(/healthy/);
  });

  it('falls back to unknown when no health data has loaded', () => {
    mockHealth(null);
    const { container } = render(
      <HealthCard integration={integration} refetchInterval={0} />,
    );

    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(statusDot(container).className).toMatch(/unknown/);
  });
});
