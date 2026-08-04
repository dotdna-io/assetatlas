// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AssetLabelTab } from '../AssetLabelTab';
import { apiClient } from '@/api/client';

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

const ASSET_ID = '11111111-1111-1111-1111-111111111111';

function buildAssetDetail(asset: Record<string, unknown>) {
  return {
    asset: {
      id: ASSET_ID,
      hostname: null,
      hostnames: [],
      fqdn: null,
      category: null,
      subcategory: null,
      os: null,
      model: null,
      manufacturer: null,
      ip: [],
      mac: [],
      mac_vendor: null,
      sn: null,
      confidence: 0,
      confidence_level: 'low',
      confidence_explanation: '',
      confidence_breakdown: null,
      last_seen: null,
      first_seen: null,
      tags: [],
      status: { is_compliant: null, is_secure: null, alert_count: 0 },
      owner: null,
      owners: [],
      location: null,
      locations: [],
      extended_attributes: {},
      ...asset,
    },
    providers: [],
    other_sources: [],
  };
}

function setupApi(opts: {
  override: { label: string | null };
  asset: Record<string, unknown>;
}) {
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.endsWith('/label-override')) {
      return Promise.resolve(opts.override);
    }
    if (url === `/assets/${ASSET_ID}`) {
      return Promise.resolve(buildAssetDetail(opts.asset));
    }
    return Promise.reject(new Error(`unexpected url: ${url}`));
  });
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('AssetLabelTab — Currently displayed as', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the manual override when one is set', async () => {
    setupApi({
      override: { label: 'my-custom-label' },
      asset: { hostname: 'host01' },
    });
    wrap(<AssetLabelTab assetId={ASSET_ID} onSuccess={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText('my-custom-label')).toBeInTheDocument();
    });
    expect(screen.getByText(/from manual override/)).toBeInTheDocument();
  });

  it('shows the hostname-derived value when there is no override', async () => {
    // Regression: #648
    setupApi({
      override: { label: null },
      asset: { hostname: 'host01' },
    });
    wrap(<AssetLabelTab assetId={ASSET_ID} onSuccess={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText('host01')).toBeInTheDocument();
    });
    expect(screen.getByText(/from hostname/)).toBeInTheDocument();
    expect(screen.queryByText(/no underlying value available/)).toBeNull();
  });

  it('falls back to MAC when hostname/fqdn are absent', async () => {
    setupApi({
      override: { label: null },
      asset: { hostname: null, fqdn: null, mac: ['aa:bb:cc:dd:ee:ff'] },
    });
    wrap(<AssetLabelTab assetId={ASSET_ID} onSuccess={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText('aa:bb:cc:dd:ee:ff')).toBeInTheDocument();
    });
    expect(screen.getByText(/from MAC/)).toBeInTheDocument();
  });

  it('shows the dash placeholder only when no override AND no underlying value', async () => {
    setupApi({
      override: { label: null },
      asset: { hostname: null, fqdn: null, mac: [], sn: null, ip: [] },
    });
    wrap(<AssetLabelTab assetId={ASSET_ID} onSuccess={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText('—')).toBeInTheDocument();
    });
    expect(screen.getByText(/no underlying value available/)).toBeInTheDocument();
  });
});
