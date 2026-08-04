// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { TypeSelector } from '../TypeSelector';
import type { Connector, ConnectorListResponse } from '@/api/integrations';

vi.mock('@/api/integrations', async () => {
  const actual = await vi.importActual<typeof import('@/api/integrations')>('@/api/integrations');
  return { ...actual, useAvailableConnectors: vi.fn() };
});

import * as integrationsApi from '@/api/integrations';

const fakeConnectors: Connector[] = [
  {
    integration_type: 'manual_upload',
    display_name: 'Manual File Upload',
    category: 'Data Import',
    description: '',
    logo_url: '/logos/assetatlas.png',
    config_schema: {},
    connector_role: 'technology',
    direct_connector: 'manual_upload',
  },
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
    integration_type: 'crowdstrike',
    display_name: 'CrowdStrike Falcon',
    category: 'EDR',
    description: '',
    logo_url: '/logos/crowdstrike.png',
    config_schema: {},
    connector_role: 'technology',
    direct_connector: 'crowdstrike',
  },
  {
    integration_type: 'palo-alto-networks',
    display_name: 'Palo Alto Networks',
    category: 'Vendor',
    description: '',
    logo_url: '/logos/paloalto.png',
    config_schema: {},
    connector_role: 'technology',
    direct_connector: null,
  },
];

function mockReturn(connectors: Connector[]) {
  const response: ConnectorListResponse = {
    success: true,
    count: connectors.length,
    connectors,
  };
  vi.mocked(integrationsApi.useAvailableConnectors).mockReturnValue({
    data: response,
    isLoading: false,
  } as any);
}

describe('TypeSelector grouping', () => {
  it('renders four sections in order: SIEM / Vendor-direct / Vendor-via-SIEM / Custom', () => {
    mockReturn(fakeConnectors);
    render(
      <TypeSelector onSelectType={vi.fn()} selectedType={null} />,
    );

    const headers = screen.getAllByText(/^(Custom|SIEM \/ Datalake|Vendor \(with direct connection\)|Vendor \(without direct connection\)) \(\d+\)$/);
    expect(headers.map(h => h.textContent)).toEqual([
      'SIEM / Datalake (1)',
      'Vendor (with direct connection) (1)',
      'Vendor (without direct connection) (1)',
      'Custom (1)',
    ]);
  });

  it('hides empty sections', () => {
    // Only a no-direct vendor tile — other sections should not render.
    mockReturn([fakeConnectors[3]]);
    render(
      <TypeSelector onSelectType={vi.fn()} selectedType={null} />,
    );

    expect(screen.queryByText(/^Manual upload/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^SIEM \/ Datalake/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Vendor \(with direct connection\)/)).not.toBeInTheDocument();
    expect(screen.getByText(/^Vendor \(without direct connection\) \(1\)$/)).toBeInTheDocument();
  });
});
