// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import type { FieldCatalogEntry, OperatorSpec } from '@/types/field-id';
import { FilterRow, type FilterCondition } from '../FilterRow';
import { isValuelessOperator, isFilterValueReady } from '../operator-specs';

const arraySpecs: OperatorSpec[] = [
  { code: 'eq', label: 'has', symbol: '=', aliases: ['=', 'is'], arity: 'one', value_type: 'text' },
  { code: 'any', label: 'has any of', symbol: 'IN', aliases: ['in'], arity: 'list', value_type: 'text' },
  { code: 'empty', label: 'is empty', symbol: 'EMPTY', aliases: [], arity: 'none', value_type: 'text' },
];

const arrayField: FieldCatalogEntry = {
  display_name: 'Data Source',
  field_id: { kind: 'static', name: 'Data Source' },
  encoded_key: 'static:Data%20Source',
  category: 'target',
  data_type: 'string_array',
  operator_specs: arraySpecs,
  is_array: true,
  is_grouped: false,
  member_sources: null,
};

const emptySpecsField: FieldCatalogEntry = {
  // operator_specs is [] (empty). Phase 3 removed the legacy operators[]
  // fallback, so such an entry now yields no operator options at all.
  display_name: 'OS',
  field_id: { kind: 'static', name: 'OS' },
  encoded_key: 'static:OS',
  category: 'target',
  data_type: 'string',
  operator_specs: [],
  is_array: false,
  is_grouped: false,
  member_sources: null,
};

function renderRow(
  field: FieldCatalogEntry,
  condition: FilterCondition,
  onChange = vi.fn(),
) {
  render(
    <FilterRow fields={[field]} condition={condition} onChange={onChange} onRemove={() => {}} />,
  );
  return onChange;
}

describe('FilterRow operator dropdown', () => {
  it('renders the resolved per-type labels, not codes', () => {
    renderRow(arrayField, { field_id: arrayField.field_id, operator: 'eq', value: '' });
    const opSelect = screen.getByRole('combobox', { name: /operator/i });
    const labels = Array.from(opSelect.querySelectorAll('option')).map((o) => o.textContent);
    expect(labels).toEqual(['has', 'has any of', 'is empty']);
  });

  it('sends the spec CODE (not a label) when the operator changes', () => {
    const onChange = renderRow(arrayField, {
      field_id: arrayField.field_id,
      operator: 'eq',
      value: 'x',
    });
    const opSelect = screen.getByRole('combobox', { name: /operator/i });
    fireEvent.change(opSelect, { target: { value: 'any' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ operator: 'any' }),
    );
  });

  it('renders no operator options when operator_specs is empty (no legacy fallback)', () => {
    // Phase 3 removed the deprecated operators[] fallback; an entry with empty
    // operator_specs now drives an empty dropdown rather than synthesizing
    // legacy-label specs.
    renderRow(emptySpecsField, { field_id: emptySpecsField.field_id, operator: '', value: '' });
    const opSelect = screen.getByRole('combobox', { name: /operator/i });
    expect(opSelect.querySelectorAll('option')).toHaveLength(0);
  });
});

describe('FilterRow value control by arity', () => {
  it('arity:none renders no value control', () => {
    renderRow(arrayField, { field_id: arrayField.field_id, operator: 'empty', value: '' });
    expect(screen.queryByPlaceholderText('Value')).toBeNull();
    expect(screen.queryByPlaceholderText(/add value/i)).toBeNull();
  });

  it('arity:one renders a single text input', () => {
    renderRow(arrayField, { field_id: arrayField.field_id, operator: 'eq', value: 'jamf' });
    expect(screen.getByPlaceholderText('Value')).toHaveValue('jamf');
  });

  it('arity:list renders a chip input that serializes chips to an array', () => {
    const onChange = renderRow(arrayField, {
      field_id: arrayField.field_id,
      operator: 'any',
      value: 'jamf', // legacy comma string tolerated on read
    });
    // Existing value renders as a chip.
    expect(screen.getByText('jamf')).toBeInTheDocument();
    const chipInput = screen.getByPlaceholderText(/add value/i);
    fireEvent.change(chipInput, { target: { value: 'sccm' } });
    fireEvent.keyDown(chipInput, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ operator: 'any', value: ['jamf', 'sccm'] }),
    );
  });

  it('removing a chip rewrites the array', () => {
    const onChange = renderRow(arrayField, {
      field_id: arrayField.field_id,
      operator: 'any',
      value: 'jamf,sccm', // legacy comma string tolerated on read
    });
    fireEvent.click(screen.getByRole('button', { name: /remove jamf/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ value: ['sccm'] }),
    );
  });

  it('keeps a list value containing a comma as a single chip (no mis-split)', () => {
    const onChange = renderRow(arrayField, {
      field_id: arrayField.field_id, operator: 'any', value: '',
    });
    const chipInput = screen.getByPlaceholderText(/add value/i);
    fireEvent.change(chipInput, { target: { value: 'Visual C++ 2015, 2017' } });
    fireEvent.keyDown(chipInput, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ value: ['Visual C++ 2015, 2017'] }),
    );
  });
});

describe('FilterRow legacy-rule round-trip', () => {
  it('selects the spec whose alias matches a stored legacy operator ("in" -> any)', () => {
    render(
      <FilterRow
        fields={[arrayField]}
        condition={{ field_id: arrayField.field_id, operator: 'in', value: 'jamf' }}
        onChange={() => {}}
        onRemove={() => {}}
      />,
    );
    const opSelect = screen.getByRole('combobox', { name: /operator/i }) as HTMLSelectElement;
    // Resolved through the alias to the "any" spec — the dropdown shows the
    // canonical code as its selected value (so a save emits the code).
    expect(opSelect.value).toBe('any');
  });

  it('keeps a stored is_not_empty resolvable to notempty without resetting', () => {
    const notemptyField: FieldCatalogEntry = {
      ...arrayField,
      operator_specs: [
        ...arraySpecs,
        { code: 'notempty', label: 'is not empty', symbol: '!EMPTY', aliases: ['is_not_empty', 'is not empty'], arity: 'none', value_type: 'text' },
      ],
    };
    render(
      <FilterRow
        fields={[notemptyField]}
        condition={{ field_id: notemptyField.field_id, operator: 'is_not_empty', value: '' }}
        onChange={() => {}}
        onRemove={() => {}}
      />,
    );
    const opSelect = screen.getByRole('combobox', { name: /operator/i }) as HTMLSelectElement;
    expect(opSelect.value).toBe('notempty');
    // arity:none -> no value control rendered.
    expect(screen.queryByPlaceholderText('Value')).toBeNull();
  });
});

describe('isValuelessOperator', () => {
  // The dropdown now emits canonical CODES, so a valueless operator picked in
  // the UI arrives as 'empty'/'notempty' (not the legacy 'is_empty'). The wizard
  // readiness gates (isFilterReady/isRowReady) depend on this — if a code isn't
  // recognized as valueless, an "is empty"-only filter is stripped on save.
  it('recognizes the canonical valueless codes the dropdown emits', () => {
    expect(isValuelessOperator('empty')).toBe(true);
    expect(isValuelessOperator('notempty')).toBe(true);
    expect(isValuelessOperator('is_true')).toBe(true);
    expect(isValuelessOperator('is_false')).toBe(true);
  });

  it('still recognizes the legacy spaced/underscored labels in stored rules', () => {
    expect(isValuelessOperator('is_empty')).toBe(true);
    expect(isValuelessOperator('is_not_empty')).toBe(true);
    expect(isValuelessOperator('is empty')).toBe(true);
    expect(isValuelessOperator('is not empty')).toBe(true);
  });

  it('returns false for value-bearing operators (codes and labels)', () => {
    expect(isValuelessOperator('eq')).toBe(false);
    expect(isValuelessOperator('any')).toBe(false);
    expect(isValuelessOperator('contains')).toBe(false);
    expect(isValuelessOperator('equals')).toBe(false);
  });
});

describe('FilterRow ChipInput dedup', () => {
  it('dedups repeated entries from a stored comma value', () => {
    renderRow(arrayField, { field_id: arrayField.field_id, operator: 'any', value: 'jamf,sccm,jamf' });
    // A legacy/hand-edited value with a duplicate must render one chip, not two
    // (two chips share a React key and a single remove drops both).
    expect(screen.getAllByText('jamf')).toHaveLength(1);
  });
});

describe('FilterRow surfaces all/none array set-operators (Phase 2)', () => {
  // An array field's operator_specs include the set-ops any/all/none. These are
  // now accepted by the tag/posture write-path enum (FilterOperator), so the
  // dropdown must surface them and emit their codes with a chip input.
  const setSpecs: OperatorSpec[] = [
    { code: 'any', label: 'has any of', symbol: 'IN', aliases: [], arity: 'list', value_type: 'text' },
    { code: 'all', label: 'has all of', symbol: 'ALL', aliases: [], arity: 'list', value_type: 'text' },
    { code: 'none', label: 'has none of', symbol: 'NONE', aliases: [], arity: 'list', value_type: 'text' },
  ];
  const setField: FieldCatalogEntry = { ...arrayField, operator_specs: setSpecs };

  it('lists the set-operators and renders a chip input for "has none of"', () => {
    renderRow(setField, { field_id: setField.field_id, operator: 'none', value: 'jamf' });
    const opSelect = screen.getByRole('combobox', { name: /operator/i }) as HTMLSelectElement;
    expect(Array.from(opSelect.querySelectorAll('option')).map((o) => o.textContent)).toEqual([
      'has any of', 'has all of', 'has none of',
    ]);
    expect(opSelect.value).toBe('none');
    expect(screen.getByText('jamf')).toBeInTheDocument(); // chip
    expect(screen.getByPlaceholderText(/add value/i)).toBeInTheDocument();
  });

  it('emits the code when an array set-operator is selected', () => {
    const onChange = renderRow(setField, { field_id: setField.field_id, operator: 'any', value: '' });
    fireEvent.change(screen.getByRole('combobox', { name: /operator/i }), { target: { value: 'all' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ operator: 'all' }));
  });
});

// ── Phase 3: between / within_last scalar-helper widgets ──────────────────
const dateSpecs: OperatorSpec[] = [
  { code: 'gte', label: 'on or after', symbol: '>=', aliases: [], arity: 'one', value_type: 'date' },
  { code: 'between', label: 'between', symbol: 'BETWEEN', aliases: [], arity: 'list', value_type: 'number' },
  { code: 'within_last', label: 'in the last', symbol: 'WITHIN', aliases: [], arity: 'one', value_type: 'date' },
];
const dateField: FieldCatalogEntry = {
  display_name: 'Last Seen',
  field_id: { kind: 'static', name: 'Last Seen' },
  encoded_key: 'static:Last%20Seen',
  category: 'advanced',
  data_type: 'timestamp',
  operator_specs: dateSpecs,
  is_array: false,
  is_grouped: false,
  member_sources: null,
};

const numericField: FieldCatalogEntry = {
  display_name: 'Confidence',
  field_id: { kind: 'static', name: 'Confidence' },
  encoded_key: 'static:Confidence',
  category: 'advanced',
  data_type: 'float',
  operator_specs: dateSpecs, // reuses the between spec; the test only selects between
  is_array: false,
  is_grouped: false,
  member_sources: null,
};

/** Stateful harness: a real onChange updates the condition so multi-control
 *  widgets (between's two inputs, within_last's amount+unit) see each other's
 *  edits. Returns the array of emitted conditions. */
function renderStateful(field: FieldCatalogEntry, initial: FilterCondition) {
  const calls: FilterCondition[] = [];
  function Wrapper() {
    const [cond, setCond] = useState(initial);
    return (
      <FilterRow
        fields={[field]}
        condition={cond}
        onChange={(c) => {
          calls.push(c);
          setCond(c);
        }}
        onRemove={() => {}}
      />
    );
  }
  render(<Wrapper />);
  return calls;
}

describe('FilterRow between/within_last value widgets', () => {
  it('between renders two date inputs (D-2) and emits a [lo, hi] array', () => {
    const calls = renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'between', value: '',
    });
    const from = screen.getByLabelText('From') as HTMLInputElement;
    const to = screen.getByLabelText('To') as HTMLInputElement;
    expect(from.type).toBe('date'); // date picker for a timestamp field
    expect(to.type).toBe('date');
    fireEvent.change(from, { target: { value: '2026-01-01' } });
    fireEvent.change(to, { target: { value: '2026-06-01' } });
    // value is a real array — the backend gets the bounds verbatim (no comma split).
    expect(calls.at(-1)?.value).toEqual(['2026-01-01', '2026-06-01']);
  });

  it('between on a numeric field renders number inputs (D-2)', () => {
    renderStateful(numericField, {
      field_id: numericField.field_id, operator: 'between', value: '',
    });
    expect((screen.getByLabelText('From') as HTMLInputElement).type).toBe('number');
    expect((screen.getByLabelText('To') as HTMLInputElement).type).toBe('number');
  });

  it('between retains a partial range in value and completes to [lo, hi]', () => {
    // Fully controlled: the partial lives in the value ([lo, '']) so the input
    // round-trips; completeness is the readiness gate's job, not the widget's.
    const calls = renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'between', value: '',
    });
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-01' } });
    expect(calls.at(-1)?.value).toEqual(['2026-01-01', '']);
    expect(isFilterValueReady('between', ['2026-01-01', ''])).toBe(false); // half-filled
    expect((screen.getByLabelText('From') as HTMLInputElement).value).toBe('2026-01-01');
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-06-01' } });
    expect(calls.at(-1)?.value).toEqual(['2026-01-01', '2026-06-01']);
    expect(isFilterValueReady('between', ['2026-01-01', '2026-06-01'])).toBe(true);
  });

  it('does not leak a bound across rows with EQUAL values on removal', () => {
    // Round-2 regression: two between rows both starting value='' (string-equal)
    // — half-fill the upper row's From, remove it; the survivor must show its
    // OWN empty value, not the deleted row's bound. A fully-controlled widget
    // (no local state) cannot leak here.
    function TwoRowList() {
      const [rows, setRows] = useState<FilterCondition[]>([
        { field_id: dateField.field_id, operator: 'between', value: '' },
        { field_id: dateField.field_id, operator: 'between', value: '' },
      ]);
      return (
        <>
          <button onClick={() => setRows((r) => r.filter((_, i) => i !== 0))}>remove-first</button>
          {rows.map((c, i) => (
            <FilterRow
              key={i}
              fields={[dateField]}
              condition={c}
              onChange={(nc) => setRows((r) => r.map((x, j) => (j === i ? nc : x)))}
              onRemove={() => {}}
            />
          ))}
        </>
      );
    }
    render(<TwoRowList />);
    fireEvent.change((screen.getAllByLabelText('From') as HTMLInputElement[])[0], { target: { value: '111' } });
    fireEvent.click(screen.getByText('remove-first'));
    expect((screen.getByLabelText('From') as HTMLInputElement).value).toBe('');
  });

  it('within_last preserves the chosen unit when the amount is cleared', () => {
    // Round-2 fix: clearing the amount must not reset the unit to the default.
    const calls = renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'within_last', value: '5w',
    });
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '' } });
    expect(calls.at(-1)?.value).toBe('w'); // unit retained, amount empty
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('w');
    expect(isFilterValueReady('within_last', 'w')).toBe(false); // amount-less -> not ready
  });

  it('reflects the surviving row\'s own bounds after a row above it is removed', () => {
    // Regression: consumers render FilterRows in an index-keyed list, so
    // removing a row shifts indices and React REUSES the lower-index instance
    // with a NEW condition.value. BetweenInputs must re-derive its bounds from
    // the changed value prop, not show the deleted row's mount-time bounds.
    function TwoRowList() {
      const [rows, setRows] = useState<FilterCondition[]>([
        { field_id: dateField.field_id, operator: 'between', value: '2020-01-01,2020-12-31' },
        { field_id: dateField.field_id, operator: 'between', value: '2099-01-01,2099-12-31' },
      ]);
      return (
        <>
          <button onClick={() => setRows((r) => r.filter((_, i) => i !== 0))}>remove-first</button>
          {rows.map((c, i) => (
            <FilterRow
              key={i}
              fields={[dateField]}
              condition={c}
              onChange={(nc) => setRows((r) => r.map((x, j) => (j === i ? nc : x)))}
              onRemove={() => {}}
            />
          ))}
        </>
      );
    }
    render(<TwoRowList />);
    fireEvent.click(screen.getByText('remove-first'));
    const froms = screen.getAllByLabelText('From') as HTMLInputElement[];
    expect(froms).toHaveLength(1);
    expect(froms[0].value).toBe('2099-01-01'); // own bounds, not the removed row's '2020-01-01'
    expect((screen.getByLabelText('To') as HTMLInputElement).value).toBe('2099-12-31');
  });

  it('within_last renders amount + unit select (s/m/h/d/w) and emits "<n><unit>"', () => {
    const calls = renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'within_last', value: '',
    });
    const unit = screen.getByLabelText('Unit') as HTMLSelectElement;
    // Units pinned to the backend resolver's set (RELATIVE_SECONDS_PER_UNIT).
    expect(Array.from(unit.options).map((o) => o.value)).toEqual(['s', 'm', 'h', 'd', 'w']);
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '30' } });
    fireEvent.change(unit, { target: { value: 'd' } });
    expect(calls.at(-1)?.value).toBe('30d');
  });

  it('parses an existing within_last value back into amount + unit', () => {
    renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'within_last', value: '24h',
    });
    expect((screen.getByLabelText('Amount') as HTMLInputElement).value).toBe('24');
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('h');
  });

  it('parses a within_last value leniently (uppercase unit) instead of resetting (D-4)', () => {
    renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'within_last', value: '90D',
    });
    expect((screen.getByLabelText('Amount') as HTMLInputElement).value).toBe('90');
    expect((screen.getByLabelText('Unit') as HTMLSelectElement).value).toBe('d');
  });

  it('clears a list value when switching a list op -> a scalar op (no stale array)', () => {
    // Review fix: switching any (value ['jamf','sccm']) -> eq must clear, else the
    // array rides on a scalar op and is sent to the backend as `col = ARRAY[...]`.
    const calls = renderStateful(arrayField, {
      field_id: arrayField.field_id, operator: 'any', value: ['jamf', 'sccm'],
    });
    fireEvent.change(screen.getByRole('combobox', { name: /operator/i }), { target: { value: 'eq' } });
    expect(calls.at(-1)?.value).toBe('');
  });

  it('clears the value when switching between -> a scalar op (no stale "lo,hi")', () => {
    const calls = renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'between', value: '2026-01-01,2026-06-01',
    });
    fireEvent.change(screen.getByRole('combobox', { name: /operator/i }), { target: { value: 'gte' } });
    expect(calls.at(-1)?.value).toBe('');
  });

  it('clears the value when switching a scalar op -> within_last (no stranded value)', () => {
    const calls = renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'gte', value: '2026-01-01',
    });
    fireEvent.change(screen.getByRole('combobox', { name: /operator/i }), { target: { value: 'within_last' } });
    expect(calls.at(-1)?.value).toBe('');
  });

  it('renders a single plain input for an arity:one op (gte) — no between/within widgets', () => {
    renderStateful(dateField, {
      field_id: dateField.field_id, operator: 'gte', value: '',
    });
    // One value control (a date input for a timestamp field, D-2), not the
    // multi-control between/within widgets.
    const input = screen.getByPlaceholderText('Value') as HTMLInputElement;
    expect(input.type).toBe('date');
    expect(screen.queryByLabelText('Unit')).toBeNull();
    expect(screen.queryByLabelText('From')).toBeNull();
  });
});

describe('FilterRow field switch (handleFieldChange)', () => {
  const osField: FieldCatalogEntry = {
    display_name: 'OS',
    field_id: { kind: 'static', name: 'OS' },
    encoded_key: 'static:OS',
    category: 'target',
    data_type: 'string',
    operator_specs: [
      { code: 'eq', label: 'equals', symbol: '=', aliases: [], arity: 'one', value_type: 'text' },
      { code: 'contains', label: 'contains', symbol: '~', aliases: [], arity: 'one', value_type: 'text' },
    ],
    is_array: false,
    is_grouped: false,
    member_sources: null,
  };

  function renderTwoFields(condition: FilterCondition) {
    const onChange = vi.fn();
    render(
      <FilterRow fields={[arrayField, osField]} condition={condition} onChange={onChange} onRemove={() => {}} />,
    );
    // Open the field SearchableDropdown (trigger has aria-label="Field") and pick OS.
    fireEvent.click(screen.getByRole('button', { name: /^field$/i }));
    fireEvent.click(screen.getByText('OS'));
    return onChange;
  }

  it('preserves the operator when the new field still supports its code', () => {
    // Data Source 'eq' -> OS (also has 'eq'): operator kept, value retained.
    const onChange = renderTwoFields({ field_id: arrayField.field_id, operator: 'eq', value: 'jamf' });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ field_id: osField.field_id, operator: 'eq', value: 'jamf' }),
    );
  });

  it('resets to the new field default operator when the current code is unsupported', () => {
    // Data Source 'any' (not on OS) -> resets to OS's default 'eq'.
    const onChange = renderTwoFields({ field_id: arrayField.field_id, operator: 'any', value: 'jamf' });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ field_id: osField.field_id, operator: 'eq' }),
    );
  });
});
