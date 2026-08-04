// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SettingsSection } from '../SettingsSection';

describe('SettingsSection', () => {
  it('renders title and children', () => {
    render(
      <SettingsSection title="Retention">
        <p>body content</p>
      </SettingsSection>,
    );
    expect(screen.getByText('Retention')).toBeInTheDocument();
    expect(screen.getByText('body content')).toBeInTheDocument();
  });

  it('renders subtitle when provided', () => {
    render(
      <SettingsSection title="Retention" subtitle="4 settings">
        <p>x</p>
      </SettingsSection>,
    );
    expect(screen.getByText('4 settings')).toBeInTheDocument();
  });

  it('omits subtitle node when not provided', () => {
    render(
      <SettingsSection title="Retention">
        <p>x</p>
      </SettingsSection>,
    );
    expect(screen.queryByText('4 settings')).toBeNull();
  });
});
