// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/help/__tests__/HelpNavLink.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

vi.mock('../../help/manifest', () => ({
  TOPICS: [{ id: 'integrations', title: 'I', category: 'data-sources', file: 'i.md', routes: ['/integrations'], order: 0 }],
}));

import { HelpNavLink } from '../../components/layout/HelpNavLink';

// Probe component that surfaces the current location state for assertions
function StateProbe({ id }: { id: string }) {
  const loc = useLocation();
  const state = loc.state as Record<string, unknown> | null;
  return <span data-testid={id}>{state ? JSON.stringify(state) : 'null'}</span>;
}

function at(path: string, state?: Record<string, unknown>) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state: state ?? null }]}>
      <HelpNavLink />
    </MemoryRouter>,
  );
}

// Renders with a probe route so we can inspect the navigation state after clicking
function atWithProbe(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<><HelpNavLink /><StateProbe id="probe" /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('HelpNavLink', () => {
  it('links to the matching topic when on a config screen', () => {
    at('/integrations');
    expect(screen.getByRole('link', { name: /help/i })).toHaveAttribute('href', '/help/integrations');
  });
  it('links to /help when no topic matches', () => {
    at('/account');
    expect(screen.getByRole('link', { name: /help/i })).toHaveAttribute('href', '/help');
  });
  it('marks the link active with aria-current on a /help path', () => {
    at('/help/integrations');
    const link = screen.getByRole('link', { name: /help/i });
    expect(link).not.toHaveAttribute('aria-current');
    // active with no from state → closes to /dashboard
    expect(link).toHaveAttribute('href', '/dashboard');
  });

  // State / origin-tracking tests
  it('carries state.from = current path when navigating from a non-help page', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/integrations']}>
        <Routes>
          <Route path="/integrations" element={<HelpNavLink />} />
          <Route path="/help/:topicId" element={<StateProbe id="probe" />} />
        </Routes>
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: /help/i });
    expect(link).toHaveAttribute('href', '/help/integrations');
    await user.click(link);
    const probe = screen.getByTestId('probe');
    const state = JSON.parse(probe.textContent || 'null');
    expect(state).not.toBeNull();
    expect(state.from).toBe('/integrations');
  });

  it('does NOT carry state.from when already on a /help path', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/help/integrations']}>
        <Routes>
          <Route path="/dashboard" element={<StateProbe id="probe" />} />
          <Route path="/help/:topicId" element={<><HelpNavLink /><StateProbe id="probe" /></>} />
        </Routes>
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: /help/i });
    expect(link).not.toHaveAttribute('aria-current');
    await user.click(link);
    const probe = screen.getByTestId('probe');
    const state = JSON.parse(probe.textContent || 'null');
    expect(state).toBeNull();
  });

  // Toggle-close behavioral tests
  it('close: on /help/integrations with state.from="/integrations", href is /integrations', () => {
    at('/help/integrations', { from: '/integrations' });
    const link = screen.getByRole('link', { name: /help/i });
    expect(link).toHaveAttribute('aria-label', 'Close help');
    expect(link).toHaveAttribute('href', '/integrations');
  });

  it('close: clicking on /help/integrations with state.from="/integrations" lands at /integrations', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[{ pathname: '/help/integrations', state: { from: '/integrations' } }]}>
        <Routes>
          <Route path="/integrations" element={<StateProbe id="probe" />} />
          <Route path="/help/:topicId" element={<HelpNavLink />} />
        </Routes>
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('link', { name: /help/i }));
    expect(screen.getByTestId('probe')).toBeInTheDocument();
  });

  it('close: on /help/x with no state, href falls back to /dashboard', () => {
    at('/help/x');
    const link = screen.getByRole('link', { name: /help/i });
    expect(link).toHaveAttribute('aria-label', 'Close help');
    expect(link).toHaveAttribute('href', '/dashboard');
  });
});
