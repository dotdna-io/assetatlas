// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AlertRulesSection } from '../AlertRulesSection';
import * as api from '@/api/alerts';
import * as auth from '@/auth/useAuth';

vi.spyOn(auth, 'useAuth').mockReturnValue({ isAdmin: true, user: {} } as any);

function setup(mockReturn: Partial<ReturnType<typeof api.useAlertRules>>) {
  vi.spyOn(api, 'useAlertRules').mockReturnValue(mockReturn as any);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AlertRulesSection searchQuery="" showDisabled={false} />
    </QueryClientProvider>
  );
}

describe('AlertRulesSection', () => {
  it('renders loading state', () => {
    setup({ isLoading: true });
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('renders error state with retry', () => {
    const refetch = vi.fn();
    setup({ isLoading: false, error: new Error('boom'), refetch } as any);
    expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
  });

  it('renders rules grouped by type', () => {
    setup({
      isLoading: false,
      data: { rules: [
        { id: '1', name: 'Sec A', type: 'security', severity: 'high', enabled: true, source: 'system', description: '' } as any,
        { id: '2', name: 'Comp A', type: 'compliance', severity: 'medium', enabled: true, source: 'user', description: '' } as any,
      ]},
    });
    // Security is the only subsection expanded by default; Compliance
    // reveals its rules after its header is clicked.
    expect(screen.getByText('Sec A')).toBeInTheDocument();
    expect(screen.queryByText('Comp A')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Compliance'));
    expect(screen.getByText('Comp A')).toBeInTheDocument();
  });

  it('renders empty state when no rules match filters', () => {
    setup({ isLoading: false, data: { rules: [] } });
    expect(screen.getByText(/no alert rules found/i)).toBeInTheDocument();
  });
});
