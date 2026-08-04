// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ReclassifyTab } from '../ReclassifyTab';
import { apiClient } from '@/api/client';

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

const ASSET_ID = '11111111-1111-1111-1111-111111111111';
const OVERRIDDEN_AT = '2026-07-14T12:00:00+00:00';

// The tab prints a local calendar date (same helper shape MergeTab uses), so
// deriving the expectation the same way keeps the assertion about "when it was
// is shown" rather than about the runner's timezone.
const EXPECTED_DATE = (() => {
  const d = new Date(OVERRIDDEN_AT);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
})();

const OVERRIDDEN = {
  asset_id: ASSET_ID,
  is_overridden: true,
  overridden_by: 'jsmith',
  overridden_at: OVERRIDDEN_AT,
  reason: 'Mislabelled by the OUI fallback',
  category_predicted: 'Server',
  subcategory_predicted: 'Linux',
};

const NOT_OVERRIDDEN = {
  asset_id: ASSET_ID,
  is_overridden: false,
  overridden_by: null,
  overridden_at: null,
  reason: null,
  category_predicted: 'Server',
  subcategory_predicted: 'Linux',
};

const CATEGORIES = {
  categories: [
    { name: 'Network', subcategories: ['Switch', 'Router'] },
    { name: 'Server', subcategories: ['Linux', 'Windows'] },
  ],
};

function setupApi(override: Record<string, unknown>) {
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.endsWith('/category-override')) return Promise.resolve(override);
    if (url === '/assets/categories') return Promise.resolve(CATEGORIES);
    return Promise.reject(new Error(`unexpected url: ${url}`));
  });
  (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({
    asset_id: ASSET_ID,
    cleared: true,
  });
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function renderTab(onSuccess = () => {}) {
  return wrap(
    <ReclassifyTab
      assetId={ASSET_ID}
      currentCategory="Network"
      currentSubcategory="Switch"
      onSuccess={onSuccess}
    />,
  );
}

describe('ReclassifyTab — override state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('says the asset was manually reclassified, by whom and when', async () => {
    // The issue itself: reopening the modal on a reclassified asset looked
    // identical to opening it on an untouched one.
    setupApi(OVERRIDDEN);

    renderTab();

    expect(await screen.findByText('Manually reclassified')).toBeInTheDocument();
    expect(screen.getByText(`${EXPECTED_DATE} by jsmith`)).toBeInTheDocument();
  });

  it('shows the reason recorded with the override', async () => {
    setupApi(OVERRIDDEN);

    renderTab();

    expect(
      await screen.findByText(/Mislabelled by the OUI fallback/),
    ).toBeInTheDocument();
  });

  it('names what the pipeline predicted rather than offering a blind revert', async () => {
    setupApi(OVERRIDDEN);

    renderTab();

    expect(await screen.findByText(/Pipeline predicted/)).toBeInTheDocument();
    expect(screen.getByText('Server → Linux')).toBeInTheDocument();
  });

  it('offers a restore control', async () => {
    setupApi(OVERRIDDEN);

    renderTab();

    expect(
      await screen.findByRole('button', { name: /Restore predicted/ }),
    ).toBeInTheDocument();
  });

  it('renders the tab exactly as before when the asset carries no override', async () => {
    setupApi(NOT_OVERRIDDEN);

    renderTab();

    // Wait for the query to settle, otherwise this asserts on the loading
    // frame and would pass even if the banner rendered unconditionally.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
    });
    expect(screen.queryByText('Manually reclassified')).toBeNull();
    expect(screen.queryByText(/Pipeline predicted/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Restore predicted/ })).toBeNull();
  });

  it('says the state could not be read rather than implying there is no override', async () => {
    // A failed read renders identically to "never reclassified" unless it says
    // so — banner and restore control both hidden on an asset that has both.
    // The service deliberately propagates the failure instead of degrading to
    // empty for exactly this reason; dropping it in the client undoes that.
    (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.endsWith('/category-override')) return Promise.reject(new Error('boom'));
      if (url === '/assets/categories') return Promise.resolve(CATEGORIES);
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });

    renderTab();

    expect(
      await screen.findByText(/Could not load the reclassification state/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Manually reclassified')).toBeNull();
  });

  it('says restoring will not change the values when nothing was predicted', async () => {
    // Rows last written before migration 143 have no snapshot; the restore
    // COALESCEs to "leave the value alone", so promising a change would be a
    // lie the user would notice and we would not.
    setupApi({ ...OVERRIDDEN, category_predicted: null, subcategory_predicted: null });

    renderTab();

    expect(await screen.findByText(/No stored prediction/)).toBeInTheDocument();
    expect(screen.queryByText(/Pipeline predicted/)).toBeNull();
    // Still worth offering: it drops the override so the next run reclassifies.
    expect(
      screen.getByRole('button', { name: /Restore predicted/ }),
    ).toBeInTheDocument();
  });
});

describe('ReclassifyTab — restoring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clears this one asset through the per-asset endpoint', async () => {
    // Undoing a single asset used to mean driving the bulk dialog with a filter
    // narrow enough to match exactly it.
    setupApi(OVERRIDDEN);
    renderTab();

    fireEvent.click(await screen.findByRole('button', { name: /Restore predicted/ }));

    await waitFor(() => {
      expect(apiClient.del).toHaveBeenCalledWith(`/assets/${ASSET_ID}/category-override`);
    });
  });

  it('tells the modal to refresh so the tab stops claiming an override', async () => {
    setupApi(OVERRIDDEN);
    const onSuccess = vi.fn();
    renderTab(onSuccess);

    fireEvent.click(await screen.findByRole('button', { name: /Restore predicted/ }));

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
    });
  });

  it('does not claim a restore happened when there was nothing left to clear', async () => {
    // Someone else cleared it between this modal's read and the click. The
    // endpoint reports a count precisely so this case is distinguishable;
    // flattening it into an unconditional "Restored" is the silent-success
    // shape the count exists to avoid.
    setupApi(OVERRIDDEN);
    (apiClient.del as ReturnType<typeof vi.fn>).mockResolvedValue({
      asset_id: ASSET_ID,
      cleared: false,
    });
    renderTab();

    fireEvent.click(await screen.findByRole('button', { name: /Restore predicted/ }));

    expect(await screen.findByText(/no longer carries an override/)).toBeInTheDocument();
    expect(screen.queryByText(/Restored to the predicted classification/)).toBeNull();
  });

  it('reports a failed restore instead of looking like it worked', async () => {
    setupApi(OVERRIDDEN);
    (apiClient.del as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'));
    const onSuccess = vi.fn();
    renderTab(onSuccess);

    fireEvent.click(await screen.findByRole('button', { name: /Restore predicted/ }));

    expect(await screen.findByText(/Restore failed/)).toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
