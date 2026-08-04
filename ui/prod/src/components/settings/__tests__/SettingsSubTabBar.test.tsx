// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsSubTabBar } from '../SettingsSubTabBar';

describe('SettingsSubTabBar', () => {
  const TABS = [
    { label: 'Retention', path: 'retention' },
    { label: 'Security', path: 'security' },
    { label: 'Logging', path: 'logging' },
  ];

  it('renders one link per tab', () => {
    render(
      <MemoryRouter initialEntries={['/settings/system/retention']}>
        <SettingsSubTabBar tabs={TABS} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Retention' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Security' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Logging' })).toBeInTheDocument();
  });
});
