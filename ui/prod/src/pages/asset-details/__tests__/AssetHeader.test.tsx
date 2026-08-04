// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { AssetHeader } from '../AssetHeader';
import type { AssetDetail, AssetStatus } from '@/api/asset-details';

function makeStatus(overrides: Partial<AssetStatus> = {}): AssetStatus {
  return { is_compliant: null, is_secure: null, alert_count: 0, in_grace: false, days_in_grace: null, ...overrides };
}

function makeAsset(status: AssetStatus): AssetDetail {
  return {
    id: 'a1', hostname: 'host-1', hostnames: ['host-1'], fqdn: null,
    category: 'Server', subcategory: null, vendor_category: null,
    os: null, model: null, manufacturer: null, ip: [], mac: [],
    mac_vendor: null, sn: null, confidence: 0.9, confidence_level: 'high',
    confidence_explanation: '', confidence_breakdown: null,
    last_seen: null, first_seen: null, tags: [], status,
    owner: null, owners: [], location: null, locations: [],
    ipam_location: null, ipam_locations: [], user: null, users: [],
    extended_attributes: {},
  };
}

describe('AssetHeader grace badge', () => {
  it('shows the In Grace badge with day count when in grace', () => {
    render(<AssetHeader asset={makeAsset(makeStatus({ in_grace: true, days_in_grace: 12 }))} />);
    expect(screen.getByText('In Grace · 12d')).toBeTruthy();
  });

  it('hides the badge when not in grace', () => {
    render(<AssetHeader asset={makeAsset(makeStatus())} />);
    expect(screen.queryByText(/In Grace/)).toBeNull();
  });
});
