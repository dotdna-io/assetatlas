// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PostureSubRuleRow } from '../PostureSubRuleRow';
import * as tagRulesApi from '@/api/tag-rules';
import type { PostureSubRule } from '@/api/posture-rules';

const fieldsData = {
  entries: [
    {
      display_name: 'Data Source',
      field_id: { kind: 'static', name: 'Data Source' },
      encoded_key: 'static:Data%20Source',
      category: 'target',
      data_type: 'string_array',
      operator_specs: [
        { code: 'eq', label: 'has', symbol: '=', aliases: [], arity: 'one', value_type: 'text' },
        { code: 'neq', label: "doesn't have", symbol: '!=', aliases: [], arity: 'one', value_type: 'text' },
        { code: 'any', label: 'has any of', symbol: 'IN', aliases: ['in'], arity: 'list', value_type: 'text' },
      ],
      is_array: true,
      is_grouped: false,
      member_sources: null,
    },
  ],
};

const subRule: PostureSubRule = {
  id: 'r1',
  name: 'Rule 1',
  description: null,
  enabled: true,
  target_filters: [
    { field_id: { kind: 'static', name: 'Data Source' }, operator: 'neq', value: 'jamf' },
  ],
  compliance_criteria: {
    logic: 'any',
    conditions: [
      { field_id: { kind: 'static', name: 'Data Source' }, operator: 'any', value: 'sccm,intune' },
    ],
  },
  output_mode: null,
  output_value: null,
} as unknown as PostureSubRule;

function renderRow() {
  vi.spyOn(tagRulesApi, 'useTagRuleFields').mockReturnValue({ data: fieldsData } as any);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PostureSubRuleRow subRule={subRule} onEdit={() => {}} />
    </QueryClientProvider>,
  );
}

describe('PostureSubRuleRow operator display', () => {
  it('shows the resolved operator label, not the raw code', () => {
    renderRow();
    // target filter "neq" -> "doesn't have"
    expect(screen.getByText(/Data Source doesn't have jamf/)).toBeInTheDocument();
    // criteria "any" -> "has any of"
    expect(screen.getByText(/Data Source has any of sccm,intune/)).toBeInTheDocument();
    // raw codes must NOT appear
    expect(screen.queryByText(/Data Source neq jamf/)).toBeNull();
    expect(screen.queryByText(/Data Source any sccm/)).toBeNull();
  });
});
