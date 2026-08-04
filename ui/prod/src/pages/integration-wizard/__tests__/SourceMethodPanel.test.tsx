// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { SourceMethodPanel } from '../SourceMethodPanel';
import type { Connector } from '@/api/integrations';

const transports: Connector[] = [
  {
    integration_type: 'splunk',
    display_name: 'Splunk Enterprise',
    category: 'SIEM',
    description: '',
    logo_url: '/logos/splunk.png',
    config_schema: {},
    connector_role: 'transport',
    direct_connector: null,
  },
  {
    integration_type: 'log_analytics',
    display_name: 'Azure Log Analytics',
    category: 'SIEM',
    description: '',
    logo_url: '/logos/microsoft sentinel.png',
    config_schema: {},
    connector_role: 'transport',
    direct_connector: null,
  },
];

describe('SourceMethodPanel', () => {
  it('shows both Direct and Via SIEM cards when direct_connector is set (existing connector)', () => {
    const tile: Connector = {
      integration_type: 'crowdstrike',
      display_name: 'CrowdStrike Falcon',
      category: 'EDR',
      description: '',
      logo_url: '/logos/crowdstrike.png',
      config_schema: {},
      connector_role: 'technology',
      direct_connector: 'crowdstrike',
    };

    render(
      <SourceMethodPanel
        selectedConnector={tile}
        transports={transports}
        onConfirm={vi.fn()}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText('Direct')).toBeInTheDocument();
    expect(screen.getByText('Via SIEM / Data Lake')).toBeInTheDocument();
  });

  it('hides Direct card and auto-selects SIEM mode for DB-only tiles (no direct_connector)', () => {
    const tile: Connector = {
      integration_type: 'palo-alto-networks',
      display_name: 'Palo Alto Networks',
      category: 'Vendor',
      description: '',
      logo_url: '/logos/paloalto.png',
      config_schema: {},
      connector_role: 'technology',
      direct_connector: null,
    };

    render(
      <SourceMethodPanel
        selectedConnector={tile}
        transports={transports}
        onConfirm={vi.fn()}
        onBack={vi.fn()}
      />,
    );

    expect(screen.queryByText('Direct')).not.toBeInTheDocument();
    // Transport picker is visible immediately because SIEM mode is auto-selected.
    expect(screen.getByText('Splunk Enterprise')).toBeInTheDocument();
    expect(screen.getByText('Azure Log Analytics')).toBeInTheDocument();
  });

  it('confirm passes the selected transport for SIEM-only tile', () => {
    const onConfirm = vi.fn();
    const tile: Connector = {
      integration_type: 'palo-alto-networks',
      display_name: 'Palo Alto Networks',
      category: 'Vendor',
      description: '',
      logo_url: '/logos/paloalto.png',
      config_schema: {},
      connector_role: 'technology',
      direct_connector: null,
    };

    render(
      <SourceMethodPanel
        selectedConnector={tile}
        transports={transports}
        onConfirm={onConfirm}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText('Splunk Enterprise'));
    fireEvent.click(screen.getByText('Continue'));

    expect(onConfirm).toHaveBeenCalledWith('splunk');
  });
});
