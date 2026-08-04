// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { Sidebar } from '../Sidebar';
import { useHealth } from '@/api/settings';
import { useAuth } from '@/auth/useAuth';

// The useHealth mock is deliberate and load-bearing — do NOT delete it as
// unused. Sidebar no longer imports useHealth (#1143 removed the watermark),
// so today this mock feeds nothing. It exists for the future: vi.mock is
// module-level, so the moment anyone re-adds a useHealth() call and renders
// the version, the mock supplies a real-looking one and the assertions below
// fire. Without it, "no version renders" would pass vacuously — there would
// be no version available to render in the first place.
vi.mock('@/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/settings')>();
  return {
    ...actual,
    useHealth: vi.fn(),
  };
});

// useAuth() throws outside an AuthProvider; the sidebar only reads the
// profile fields and logout off it.
vi.mock('@/auth/useAuth', () => ({ useAuth: vi.fn() }));

const mockHealth = useHealth as ReturnType<typeof vi.fn>;
const mockUseAuth = useAuth as ReturnType<typeof vi.fn>;

function renderSidebar() {
  // Sidebar has no react-query hooks left. The provider stays so that
  // re-adding one fails on the version assertion rather than on a missing
  // QueryClient.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/dashboard']}>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockHealth.mockReturnValue({ data: { application: { version: '2026.31.12' } } });
    mockUseAuth.mockReturnValue({
      user: { full_name: 'Ada Lovelace', username: 'ada', email: 'ada@example.com' },
      logout: vi.fn(),
      isAdmin: true,
    });
  });

  // #1143: the version rendered as a `v${version}` watermark outside <nav>,
  // styled position:fixed bottom-right with z-index 9999, so it floated over
  // every page in the app — including over modals. The About page
  // (/settings/system/about) is the only place a version belongs.
  it('renders no version string even when health reports one', () => {
    const { container } = renderSidebar();

    // Guard: the sidebar really did mount, so the negative assertions below
    // are not passing against an empty render.
    expect(screen.getByRole('link', { name: 'Home' })).toBeInTheDocument();

    // The old watermark's exact output shape...
    expect(screen.queryByText(/^v\d/)).toBeNull();
    // ...and any reformatting of it.
    expect(container).not.toHaveTextContent('2026.31.12');
  });

  // Hub and Settings sit behind admin-only APIs. They stay visible so the
  // capability is discoverable, but a standard user cannot open them.
  const ADMIN_ONLY = ['Hub', 'Settings'];
  const ALWAYS_OPEN = ['Home', 'Explore', 'Reporting', 'Alerts', 'Rules'];

  it('renders the admin-only destinations inert for a standard user', () => {
    mockUseAuth.mockReturnValue({
      user: { full_name: 'Ada Lovelace', username: 'ada', email: 'ada@example.com' },
      logout: vi.fn(),
      isAdmin: false,
    });
    renderSidebar();

    for (const label of ADMIN_ONLY) {
      const el = screen.getByTitle(`${label} requires administrator access`);
      // A span rather than an anchor: an anchor stays keyboard-focusable and
      // announces navigation that cannot happen.
      expect(el.tagName).toBe('SPAN');
      expect(el).toHaveAttribute('aria-disabled', 'true');
      expect(el).not.toHaveAttribute('href');
    }

    // The rest of the nav is untouched — this is not "hide everything".
    for (const label of ALWAYS_OPEN) {
      expect(screen.getByText(label).closest('a')).not.toBeNull();
    }
  });

  it('renders them as working links for an admin', () => {
    renderSidebar(); // beforeEach already sets isAdmin: true

    expect(screen.getByText('Hub').closest('a')).toHaveAttribute('href', '/integrations');
    expect(screen.getByText('Settings').closest('a')).toHaveAttribute('href', '/settings');
    expect(screen.queryByTitle('Hub requires administrator access')).not.toBeInTheDocument();
  });
});
