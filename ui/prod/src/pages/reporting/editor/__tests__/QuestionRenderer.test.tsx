// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QuestionRenderer } from '../QuestionRenderer';
import type { QuestionSection } from '../../data/chartQuestions';

const assetFields = [
  {
    name: 'OS',
    value: 'static:OS',
    field_id: { kind: 'static', name: 'OS' } as const,
    type: 'string',
    category: 'Asset Properties',
  },
  {
    name: 'Last Seen',
    // value IS the encoded_key (encode(field_id)); the space must be %20 so it
    // matches FilterRow's encodeFieldId(condition.field_id) lookup.
    value: 'static:Last%20Seen',
    field_id: { kind: 'static', name: 'Last Seen' } as const,
    type: 'timestamp',
    category: 'Asset Properties',
  },
];

const sections: QuestionSection[] = [
  {
    title: 'Filters',
    fields: [{ id: 'filters', label: 'Filters', type: 'filter-builder' }],
  },
] as unknown as QuestionSection[];

function renderBuilder(value: unknown, onAnswerChange = vi.fn()) {
  render(
    <QuestionRenderer
      sections={sections}
      answers={{ filters: value }}
      chartMeta={{}}
      assetFields={assetFields}
      onAnswerChange={onAnswerChange}
    />,
  );
  return onAnswerChange;
}

describe('reporting filter-builder (canonical operator_specs)', () => {
  it('resolves an existing legacy-label condition to its canonical code', () => {
    // Phase 3 unification: QuestionRenderer synthesizes operator_specs with
    // canonical codes + backward-compat aliases, so a rule saved with the old
    // spaced label "equals" still resolves — the dropdown shows the canonical
    // "eq" option (and re-saves the code on edit).
    renderBuilder([{ field_id: assetFields[0].field_id, operator: 'equals', value: 'Windows' }]);
    const opSelect = screen.getByRole('combobox', { name: /operator/i }) as HTMLSelectElement;
    expect(opSelect.value).toBe('eq');
    expect(screen.getByDisplayValue('Windows')).toBeInTheDocument();
  });

  it('adds a new condition seeded with the canonical default code', () => {
    const onAnswerChange = renderBuilder(undefined);
    fireEvent.click(screen.getByRole('button', { name: /add filter/i }));
    expect(onAnswerChange).toHaveBeenCalledWith(
      'filters',
      [expect.objectContaining({ operator: 'eq', value: '' })],
    );
  });

  it('emits a canonical code when the operator changes (round-trip)', () => {
    const onAnswerChange = renderBuilder([
      { field_id: assetFields[0].field_id, operator: 'eq', value: 'Windows' },
    ]);
    const opSelect = screen.getByRole('combobox', { name: /operator/i });
    fireEvent.change(opSelect, { target: { value: 'neq' } });
    expect(onAnswerChange).toHaveBeenCalledWith(
      'filters',
      [expect.objectContaining({ operator: 'neq' })],
    );
  });

  it('offers set-membership ops (any/none) for a string field but not all (E-1)', () => {
    renderBuilder([{ field_id: assetFields[0].field_id, operator: 'eq', value: '' }]);
    const opSelect = screen.getByRole('combobox', { name: /operator/i }) as HTMLSelectElement;
    const codes = Array.from(opSelect.options).map((o) => o.value);
    expect(codes).toEqual(expect.arrayContaining(['any', 'none']));
    expect(codes).not.toContain('all');         // charts backend has no ALL op
    expect(codes).not.toContain('within_last');
  });

  it('offers between (not within_last) for a date field (E-1)', () => {
    renderBuilder([{ field_id: assetFields[1].field_id, operator: 'gte', value: '' }]);
    const opSelect = screen.getByRole('combobox', { name: /operator/i }) as HTMLSelectElement;
    const codes = Array.from(opSelect.options).map((o) => o.value);
    expect(codes).toContain('between');
    expect(codes).not.toContain('within_last'); // charts backend has no WITHIN op
  });
});
