// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { MonitoringGroupPage } from '../MonitoringGroupPage';

// Both sub-tabs are static now. The Dashboards tab hosts the enable/disable
// toggle (MonitoringEnableBar), so it must stay visible even when the dashboards
// are currently off — otherwise there'd be no way to switch them back on.
// (Previously it was hidden when monitoring was unavailable, which is why the
// old test mocked useMonitoringAvailable; the group no longer subscribes to it.)
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/settings/monitoring/forwarding']}>
      <MonitoringGroupPage />
    </MemoryRouter>,
  );
}

describe('MonitoringGroupPage', () => {
  it('always shows the Forwarding tab', () => {
    renderPage();
    expect(screen.getByRole('link', { name: 'Forwarding' })).toBeInTheDocument();
  });

  it('always shows the Dashboards tab (it hosts the enable/disable toggle)', () => {
    renderPage();
    expect(screen.getByRole('link', { name: 'Dashboards' })).toBeInTheDocument();
  });
});
