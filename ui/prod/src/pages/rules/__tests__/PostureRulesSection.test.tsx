// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

import { PostureRulesSection } from '../PostureRulesSection';
import { apiClient } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
}));

const MOCK_FIELDS_RESPONSE = {
  entries: [
    {
      display_name: 'Category',
      field_id: { kind: 'static', name: 'Category' },
      encoded_key: 'static:Category',
      category: 'target',
      data_type: 'STRING',
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
    {
      display_name: 'Hostname',
      field_id: { kind: 'static', name: 'Hostname' },
      encoded_key: 'static:Hostname',
      category: 'target',
      data_type: 'STRING_ARRAY',
      is_array: true,
      is_grouped: false,
      member_sources: null,
    },
    {
      display_name: 'Data Source Category',
      field_id: { kind: 'static', name: 'Data Source Category' },
      encoded_key: 'static:Data%20Source%20Category',
      category: 'advanced',
      data_type: 'STRING_ARRAY',
      is_array: true,
      is_grouped: false,
      member_sources: null,
    },
  ],
};

function mockApiGet(categoriesResponse: unknown) {
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.includes('/tag-rules/fields')) return Promise.resolve(MOCK_FIELDS_RESPONSE);
    return Promise.resolve(categoriesResponse);
  });
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const MOCK_CATEGORIES = [
  {
    id: 'secured',
    name: 'Secured',
    description: 'EDR/AV coverage',
    icon: 'M1',
    applicable_categories: ['server'],
    display_order: 1,
    sub_rules: [],
    stats: { pass: 10, fail: 2, n_a: 0 },
    last_evaluated_at: null,
  },
  {
    id: 'vulnerability_scanning',
    name: 'Vulnerability Scanning',
    description: 'VA scan coverage',
    icon: 'M2',
    applicable_categories: ['server'],
    display_order: 2,
    sub_rules: [],
    stats: { pass: 0, fail: 0, n_a: 0 },
    last_evaluated_at: null,
  },
];

describe('PostureRulesSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders each category as a card under a Posture sub-header', async () => {
    mockApiGet({ categories: MOCK_CATEGORIES });
    wrap(<PostureRulesSection searchQuery="" />);

    await waitFor(() => expect(screen.getByText('Secured')).toBeInTheDocument());
    // The collapsible sub-header now carries its count, so its accessible
    // name is "Posture <n> categories". Anchored on that shape rather than a
    // bare /posture/ so the per-card blue "Posture" labels — also role=button
    // — stay excluded. "categories", not "rules": the group holds categories.
    expect(
      screen.getByRole('button', { name: /^Posture \d+ categor(y|ies)$/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('Vulnerability Scanning')).toBeInTheDocument();
  });

  it('does not render category cards while loading', () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    wrap(<PostureRulesSection searchQuery="" />);
    expect(screen.queryByText('Secured')).not.toBeInTheDocument();
  });

  it('shows an error message when fetch fails', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network error'));
    wrap(<PostureRulesSection searchQuery="" />);

    await waitFor(() =>
      expect(screen.getByText('Failed to load posture rules.')).toBeInTheDocument(),
    );
  });

  it('filters categories by the search query', async () => {
    mockApiGet({ categories: MOCK_CATEGORIES });
    wrap(<PostureRulesSection searchQuery="vulnerability" />);

    await waitFor(() =>
      expect(screen.getByText('Vulnerability Scanning')).toBeInTheDocument(),
    );
    // "Secured" doesn't match the query, so it's filtered out.
    expect(screen.queryByText('Secured')).not.toBeInTheDocument();
  });

  it('shows the empty rules message when a card is expanded', async () => {
    mockApiGet({ categories: [MOCK_CATEGORIES[0]] });
    wrap(<PostureRulesSection searchQuery="" />);

    await waitFor(() => expect(screen.getByText('Secured')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Secured'));

    expect(
      screen.getByText('No rules configured — all assets show N/A for this category.'),
    ).toBeInTheDocument();
  });

  it('renders rule rows when a card is expanded', async () => {
    const categoryWithRules = {
      ...MOCK_CATEGORIES[0],
      last_evaluated_at: '2026-04-19T13:45:00+00:00',
      sub_rules: [
        {
          id: 'sr1',
          category_id: 'secured',
          name: 'Workstations need EDR',
          description: null,
          enabled: true,
          target_filters: [{ field_id: { kind: 'static', name: 'Category' }, operator: 'equals', value: 'workstation' }],
          compliance_criteria: {
            logic: 'any' as const,
            conditions: [{ field_id: { kind: 'static', name: 'Data Source Category' }, operator: 'contains', value: 'EDR' }],
          },
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        },
      ],
    };

    mockApiGet({ categories: [categoryWithRules] });
    wrap(<PostureRulesSection searchQuery="" />);

    await waitFor(() => expect(screen.getByText('Secured')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Secured'));

    expect(screen.getByText('Workstations need EDR')).toBeInTheDocument();
  });
});
