// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AlertCard } from '../AlertCard';
import * as auth from '@/auth/useAuth';
import type { AlertRule } from '@/api/alerts';

const systemRule: AlertRule = {
  id: 's1', name: 'SysRule', slug: 'sys', description: '',
  type: 'security', severity: 'high', enabled: true, source: 'system',
  query: 'SELECT ...', target_filters: null, compliance_criteria: null,
  threshold: null, silence_duration: null, created_by: null,
  created_at: '', updated_at: '',
};

const userRule: AlertRule = {
  ...systemRule, id: 'u1', name: 'UserRule', source: 'user',
  query: null, target_filters: [{ field: 'static:Hostname', op: '=', value: 'y' }],
};

function mockAdmin(isAdmin: boolean) {
  vi.spyOn(auth, 'useAuth').mockReturnValue({ isAdmin, user: {} } as any);
}

function renderCard(rule: AlertRule) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AlertCard rule={rule} />
    </QueryClientProvider>,
  );
}

describe('AlertCard', () => {
  it('system rule as admin: no edit or delete, toggle enabled', () => {
    mockAdmin(true);
    renderCard(systemRule);
    expect(screen.queryByTitle('Delete')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Edit')).not.toBeInTheDocument();
    expect(screen.getByRole('switch')).not.toBeDisabled();
  });

  it('user rule as admin: edit + delete visible, toggle enabled', () => {
    mockAdmin(true);
    renderCard(userRule);
    expect(screen.getByTitle('Delete')).toBeInTheDocument();
    expect(screen.getByTitle('Edit')).toBeInTheDocument();
    expect(screen.getByRole('switch')).not.toBeDisabled();
  });

  it('non-admin: no edit, no delete, toggle disabled', () => {
    mockAdmin(false);
    renderCard(userRule);
    expect(screen.queryByTitle('Delete')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Edit')).not.toBeInTheDocument();
    expect(screen.getByRole('switch')).toBeDisabled();
  });

  it('renders without crashing when threshold has no condition key (seeded system rule shape)', () => {
    mockAdmin(true);
    const ruleWithMinThreshold = { ...systemRule, threshold: { min: 1 } as any };
    expect(() => renderCard(ruleWithMinThreshold)).not.toThrow();
    expect(screen.getByText('SysRule')).toBeInTheDocument();
  });
});

it('edit button opens inline form with rule fields pre-populated', () => {
  mockAdmin(true);
  renderCard({ ...userRule, name: 'EditMe' });
  fireEvent.click(screen.getByTitle('Edit'));
  expect(screen.getByDisplayValue('EditMe')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
});

it('save calls useUpdateAlertRule', async () => {
  mockAdmin(true);
  const mockUpdate = vi.fn();
  const alertsApi = await import('@/api/alerts');
  vi.spyOn(alertsApi, 'useUpdateAlertRule').mockReturnValue({
    mutate: mockUpdate, isPending: false,
  } as any);
  renderCard(userRule);
  fireEvent.click(screen.getByTitle('Edit'));
  fireEvent.click(screen.getByRole('button', { name: /save/i }));
  expect(mockUpdate).toHaveBeenCalledWith(
    expect.objectContaining({ id: userRule.id }),
    expect.anything(),
  );
});

// --- added for #584 §2 (#556) ---
// TargetFilters fetches fields and its embedded PreviewCounter calls
// tagRulesApi.preview; stub both so edit mode renders offline.
vi.mock('@/api/tag-rules', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/tag-rules')>();
  return {
    ...actual,
    useTagRuleFields: () => ({ data: { entries: [] }, isLoading: false }),
    tagRulesApi: { ...actual.tagRulesApi, preview: vi.fn(async () => ({ total_matching: 0 })) },
  };
});

describe('AlertCard full edit (#556)', () => {
  it('save payload includes target_filters, compliance_criteria and threshold', async () => {
    mockAdmin(true);
    const updateMutate = vi.fn();
    const alertsApi = await import('@/api/alerts');
    vi.spyOn(alertsApi, 'useUpdateAlertRule').mockReturnValue({
      mutate: updateMutate, isPending: false,
    } as any);
    renderCard({
      ...userRule,
      threshold: { condition: 'exceeds', value: 80, unit: 'percent' },
    });
    fireEvent.click(screen.getByTitle('Edit'));
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(updateMutate).toHaveBeenCalled();
    const { data } = updateMutate.mock.calls[0][0];
    expect(data.target_filters).toEqual([{ field: 'static:Hostname', op: '=', value: 'y' }]);
    expect(data.threshold).toEqual({ condition: 'exceeds', value: 80, unit: 'percent' });
    expect(data.compliance_criteria).toBeNull(); // security rule → no criteria
  });

  it('renders the filter editor and threshold editor in edit mode', () => {
    mockAdmin(true);
    renderCard(userRule);
    fireEvent.click(screen.getByTitle('Edit'));
    expect(screen.getByText('No threshold')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add filter/i })).toBeInTheDocument();
  });

  it('drops incomplete compliance-criteria rows from the save payload', async () => {
    mockAdmin(true);
    const updateMutate = vi.fn();
    const alertsApi = await import('@/api/alerts');
    vi.spyOn(alertsApi, 'useUpdateAlertRule').mockReturnValue({
      mutate: updateMutate, isPending: false,
    } as any);
    renderCard({
      ...userRule,
      type: 'compliance',
      compliance_criteria: {
        logic: 'all',
        conditions: [
          { field: 'static:Model', op: '=', value: 'ok' },
          { field: 'static:Owner', op: '=', value: '' }, // incomplete → must be dropped
        ],
      },
    });
    fireEvent.click(screen.getByTitle('Edit'));
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    const { data } = updateMutate.mock.calls[0][0];
    expect(data.compliance_criteria).toEqual({
      logic: 'all',
      conditions: [{ field: 'static:Model', op: '=', value: 'ok' }],
    });
  });

  it('disables Save when every target filter is removed (no empty target_filters POST)', () => {
    mockAdmin(true);
    renderCard(userRule);
    fireEvent.click(screen.getByTitle('Edit'));
    expect(screen.getByRole('button', { name: /save changes/i })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /remove filter/i }));
    expect(screen.getByRole('button', { name: /save changes/i })).toBeDisabled();
  });
});

const mlRule: AlertRule = {
  ...systemRule, id: 'ml1', name: 'ML newcomer', slug: 'anomaly-ml-newcomer',
  evaluation_type: 'anomaly', detector_key: 'ml_newcomer',
  threshold: { pct_medium: 0.5, pct_high: 0.1, min_fit_rows: 500 } as any,
};

// The sensitivity editor lives behind the card's disclosure (same collapse
// as every other card) — render, then expand via the header.
function renderMlCard(rule: AlertRule) {
  const result = renderCard(rule);
  fireEvent.click(screen.getByText(rule.name));
  return result;
}

describe('AlertCard description expand (Rules density rework)', () => {
  it('hides the description until the row is clicked, then reveals it', () => {
    mockAdmin(true);
    const desc = 'A detailed rationale that should not clutter the row.';
    renderCard({ ...systemRule, description: desc });
    expect(screen.queryByText(desc)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('SysRule')); // click the row
    expect(screen.getByText(desc)).toBeInTheDocument();
  });

  it('clicking the toggle control does not expand the description', async () => {
    mockAdmin(true);
    const alertsApi = await import('@/api/alerts');
    vi.spyOn(alertsApi, 'useToggleAlertRule').mockReturnValue({
      mutate: vi.fn(), isPending: false,
    } as any);
    const desc = 'Detail that stays hidden when toggling.';
    renderCard({ ...systemRule, description: desc });
    fireEvent.click(screen.getByRole('switch'));
    expect(screen.queryByText(desc)).not.toBeInTheDocument();
  });
});

// #951 replaced the two raw number inputs with plain-language sliders; a follow-up
// merged them into one two-handle track (High + Medium on a shared scale). Backend is
// unchanged — Save still PATCHes {pct_medium, pct_high} as percentages through
// useUpdateAnomalyThreshold. These assert slider semantics, not pixel positions.
describe('AlertCard ML sensitivity slider (#951, merged to one track)', () => {
  it('renders one two-handle track for an ML system rule as admin — no raw percentages', () => {
    mockAdmin(true);
    renderMlCard(mlRule);
    // Two handles (High + Medium) share a single track.
    expect(screen.getByRole('slider', { name: /high alert sensitivity/i })).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: /medium alert sensitivity/i })).toBeInTheDocument();
    expect(screen.getAllByRole('slider')).toHaveLength(2);
    // The old raw number inputs are gone and the stored cutoffs are not shown as text.
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(screen.queryByText('0.5')).not.toBeInTheDocument();
    expect(screen.queryByText('0.1')).not.toBeInTheDocument();
    // One shared track → a single pair of plain end-words (no per-slider duplication).
    expect(screen.getByText('Only the most unusual')).toBeInTheDocument();
    expect(screen.getByText('Flag anything slightly odd')).toBeInTheDocument();
    // A plain-language caption describes each tier in words (still no numbers): at the
    // defaults High reads "only the most extreme outliers", Medium "moderately unusual assets".
    expect(screen.getByText(/only the most extreme outliers/i)).toBeInTheDocument();
    expect(screen.getByText(/moderately unusual assets/i)).toBeInTheDocument();
    // Save is always available once loaded (no validation-disable / error state).
    expect(screen.getByRole('button', { name: /save sensitivity/i })).toBeEnabled();
  });

  it('does not render for non-admins (the anomaly-threshold route is admin-only)', () => {
    mockAdmin(false);
    renderMlCard(mlRule);
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save sensitivity/i })).not.toBeInTheDocument();
  });

  it('is absent on non-ML rules (keys off detector_key, not the anomaly type)', () => {
    mockAdmin(true);
    renderCard(userRule);
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save sensitivity/i })).not.toBeInTheDocument();
  });

  it('saves the loaded cutoffs unchanged through useUpdateAnomalyThreshold', async () => {
    mockAdmin(true);
    const mutate = vi.fn();
    const alertsApi = await import('@/api/alerts');
    vi.spyOn(alertsApi, 'useUpdateAnomalyThreshold').mockReturnValue({
      mutate, isPending: false, isError: false, isSuccess: false,
    } as any);
    renderMlCard(mlRule);
    fireEvent.click(screen.getByRole('button', { name: /save sensitivity/i }));
    // Saving without touching a slider round-trips the stored cutoffs: the log-scale
    // mapping places 0.5 / 0.1 at their natural positions. Payload shape is unchanged.
    expect(mutate).toHaveBeenCalledWith({ id: 'ml1', data: { pct_medium: 0.5, pct_high: 0.1 } });
  });

  it('drags High down with Medium when Medium moves below it (High ≤ Medium holds)', async () => {
    mockAdmin(true);
    const mutate = vi.fn();
    const alertsApi = await import('@/api/alerts');
    vi.spyOn(alertsApi, 'useUpdateAnomalyThreshold').mockReturnValue({
      mutate, isPending: false, isError: false, isSuccess: false,
    } as any);
    renderMlCard(mlRule);
    // Drag Medium to the strictest end — below High's initial position.
    fireEvent.change(screen.getByRole('slider', { name: /medium alert sensitivity/i }), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /save sensitivity/i }));
    const { data } = mutate.mock.calls[0][0];
    expect(data.pct_high).toBeLessThanOrEqual(data.pct_medium);
  });

  it('clamps High to Medium when High is dragged past it (High can never be looser)', async () => {
    mockAdmin(true);
    const mutate = vi.fn();
    const alertsApi = await import('@/api/alerts');
    vi.spyOn(alertsApi, 'useUpdateAnomalyThreshold').mockReturnValue({
      mutate, isPending: false, isError: false, isSuccess: false,
    } as any);
    renderMlCard(mlRule);
    // Push High to the most-sensitive end — past Medium.
    fireEvent.change(screen.getByRole('slider', { name: /high alert sensitivity/i }), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: /save sensitivity/i }));
    const { data } = mutate.mock.calls[0][0];
    expect(data.pct_high).toBeLessThanOrEqual(data.pct_medium);
  });

  it('loads an already-invalid stored rule (high looser than medium) as valid — Save enabled', () => {
    mockAdmin(true);
    // The pre-#951 shape allowed pct_high > pct_medium; the sliders must not expose an
    // invalid/error state. High is seeded clamped to Medium, so Save stays enabled.
    renderMlCard({ ...mlRule, threshold: { pct_medium: 0.1, pct_high: 0.5 } as any });
    expect(screen.getByRole('button', { name: /save sensitivity/i })).toBeEnabled();
  });

  it('clears the settled "Saved" state when a slider is moved after a save', async () => {
    mockAdmin(true);
    const reset = vi.fn();
    const alertsApi = await import('@/api/alerts');
    // `data` and not just `isSuccess`: success is shown by the Button's flash,
    // which restarts on the identity of the value passed to `success`.
    vi.spyOn(alertsApi, 'useUpdateAnomalyThreshold').mockReturnValue({
      mutate: vi.fn(), reset, isPending: false, isError: false, isSuccess: true,
      data: { id: 'ml-1' },
    } as any);
    renderMlCard(mlRule);
    // Settled success shows on the button itself, not as a label beside it.
    expect(screen.getByRole('button', { name: /saved/i })).toBeInTheDocument();
    fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '0.4' } });
    expect(reset).toHaveBeenCalled();                          // stale state cleared on edit
  });
});
