// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/help/__tests__/HelpPage.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('@/basePath', () => ({ BASE_PATH: '', assetUrl: (p: string) => p }));

vi.mock('../manifest', () => {
  const TOPICS = [
    { id: 'integrations', title: 'Connecting Data Sources', category: 'data-sources', file: 'i.md', routes: ['/integrations'], order: 0 },
    { id: 'oidc', title: 'OIDC SSO', category: 'access-security', file: 'o.md', routes: [], order: 0 },
  ];
  const C: Record<string,string> = { 'i.md': '# Integrations\nBody text alpha.', 'o.md': '# OIDC\nBody beta.' };
  return { TOPICS, getTopicById: (id: string) => TOPICS.find(t => t.id === id),
           getTopicContent: (t: { file: string }) => C[t.file] ?? '' };
});

import { HelpPage } from '../HelpPage';

function renderAt(path: string, state?: Record<string, unknown>) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state: state ?? null }]}>
      <Routes>
        <Route path="/help" element={<HelpPage />} />
        <Route path="/help/:topicId" element={<HelpPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('HelpPage', () => {
  it('renders the categorized TOC', () => {
    renderAt('/help');
    expect(screen.getByText('Data Sources')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Connecting Data Sources' })).toBeInTheDocument();
  });
  it('renders the selected topic body', () => {
    renderAt('/help/integrations');
    expect(screen.getByText('Body text alpha.')).toBeInTheDocument();
  });
  it('search filters the TOC', async () => {
    renderAt('/help');
    await userEvent.type(screen.getByPlaceholderText('Search help…'), 'beta');
    expect(screen.getByRole('link', { name: 'OIDC SSO' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Connecting Data Sources' })).toBeNull();
  });
  it('unknown topic id falls back to home copy', () => {
    renderAt('/help/__nope__');
    expect(screen.getByText(/select a topic/i)).toBeInTheDocument();
  });

  // Back link tests
  it('renders a back link', () => {
    renderAt('/help/integrations');
    expect(screen.getByRole('link', { name: '← Back' })).toBeInTheDocument();
  });
  it('back link points to the from origin when provided via router state', () => {
    renderAt('/help/integrations', { from: '/integrations' });
    const back = screen.getByRole('link', { name: '← Back' });
    expect(back).toHaveAttribute('href', '/integrations');
  });
  it('back link falls back to /dashboard when no state is present', () => {
    renderAt('/help/integrations');
    const back = screen.getByRole('link', { name: '← Back' });
    expect(back).toHaveAttribute('href', '/dashboard');
  });

  it('renders an open-in-new-tab link with correct attributes', () => {
    renderAt('/help/integrations');
    const newTab = screen.getByRole('link', { name: /open.*new tab/i });
    expect(newTab).toHaveAttribute('target', '_blank');
    expect(newTab.getAttribute('rel')).toContain('noopener');
    expect(newTab.getAttribute('href')).toMatch(/\/help\/integrations$/);
  });
});
