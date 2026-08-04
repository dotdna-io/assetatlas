// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AboutTab } from '../AboutTab';
import {
  useUpdateCheck,
  useForceUpdateCheck,
  useSetUpdateCheckEnabled,
  useHealth,
  useHealthAbout,
  useSuggestionsHealth,
  type UpdateCheckResponse,
} from '@/api/settings';
import { useAuth } from '@/auth';

vi.mock('@/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/settings')>();
  return {
    ...actual,
    useHealth: vi.fn(),
    useHealthAbout: vi.fn(),
    useSuggestionsHealth: vi.fn(),
    useUpdateCheck: vi.fn(),
    useForceUpdateCheck: vi.fn(),
    useSetUpdateCheckEnabled: vi.fn(),
  };
});
vi.mock('@/auth', () => ({ useAuth: vi.fn() }));

const mockHealth = useHealth as ReturnType<typeof vi.fn>;
const mockAbout = useHealthAbout as ReturnType<typeof vi.fn>;
const mockSuggestions = useSuggestionsHealth as ReturnType<typeof vi.fn>;
const mockUpdateCheck = useUpdateCheck as ReturnType<typeof vi.fn>;
const mockForceCheck = useForceUpdateCheck as ReturnType<typeof vi.fn>;
const mockSetEnabled = useSetUpdateCheckEnabled as ReturnType<typeof vi.fn>;
const mockUseAuth = useAuth as ReturnType<typeof vi.fn>;

const UPDATE_AVAILABLE: UpdateCheckResponse = {
  enabled: true, current_version: '2026.27.5', latest_version: '2026.28.1',
  update_available: true, checked_at: '2026-07-09T00:00:00Z',
};
const DISABLED: UpdateCheckResponse = {
  enabled: false, current_version: '2026.27.5', latest_version: null,
  update_available: false, checked_at: null,
};

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><AboutTab /></QueryClientProvider>,
  );
}

describe('AboutTab update check', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockHealth.mockReturnValue({ data: { application: { version: '2026.27.5' } } });
    mockAbout.mockReturnValue({ data: {} });
    mockSuggestions.mockReturnValue({ data: undefined, isError: false });
    mockForceCheck.mockReturnValue({ mutate: vi.fn(), isPending: false, isSuccess: false });
    mockSetEnabled.mockReturnValue({ mutate: vi.fn(), isPending: false });
    mockUseAuth.mockReturnValue({ isAdmin: true });
  });

  it('shows the "update available" badge when an update exists', () => {
    mockUpdateCheck.mockReturnValue({ data: UPDATE_AVAILABLE });
    renderTab();
    expect(screen.getByText(/update available/i)).toBeInTheDocument();
    expect(screen.getByText(/2026\.28\.1/)).toBeInTheDocument();
  });

  // The bug this feature fixes: with the master switch off there was no way to
  // turn it on from the UI. Admins must now see the toggle (off) so they can.
  it('shows the enable toggle (off) to admins when the check is disabled', () => {
    mockUpdateCheck.mockReturnValue({ data: DISABLED });
    renderTab();
    const toggle = screen.getByRole('switch');
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    // ...but nothing that only makes sense once enabled.
    expect(screen.queryByText(/update available/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /check now/i })).not.toBeInTheDocument();
  });

  it('enables the update check when an admin flips the toggle on', () => {
    const mutate = vi.fn();
    mockSetEnabled.mockReturnValue({ mutate, isPending: false });
    mockUpdateCheck.mockReturnValue({ data: DISABLED });
    renderTab();
    fireEvent.click(screen.getByRole('switch'));
    expect(mutate).toHaveBeenCalledWith(true);
  });

  it('shows the toggle (on) plus Check now when the check is enabled', () => {
    mockUpdateCheck.mockReturnValue({ data: UPDATE_AVAILABLE });
    renderTab();
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('button', { name: /check now/i })).toBeInTheDocument();
  });

  it('hides the toggle and Check now for non-admins but still shows the badge', () => {
    mockUseAuth.mockReturnValue({ isAdmin: false });
    mockUpdateCheck.mockReturnValue({ data: UPDATE_AVAILABLE });
    renderTab();
    expect(screen.getByText(/update available/i)).toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /check now/i })).not.toBeInTheDocument();
  });

  it('fires the force check when an admin clicks "Check now"', () => {
    const mutate = vi.fn();
    mockForceCheck.mockReturnValue({ mutate, isPending: false, isSuccess: false });
    mockUpdateCheck.mockReturnValue({ data: UPDATE_AVAILABLE });
    renderTab();
    fireEvent.click(screen.getByRole('button', { name: /check now/i }));
    expect(mutate).toHaveBeenCalledTimes(1);
  });
});
