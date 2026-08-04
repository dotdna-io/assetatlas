// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState } from 'react';

import { encodeFieldId, type FieldCatalogEntry, type FieldId } from '@/types/field-id';
import { SearchableDropdown, type DropdownOption } from '@/components/ui/SearchableDropdown';
import { deriveOperatorSpecs, specFor } from './operator-specs';
import styles from './FilterRow.module.css';

interface FilterCondition {
  field_id: FieldId;
  operator: string;
  // List operators (any/all/none) and `between` carry their value as a real
  // string[] end-to-end (no comma-joining), so a member value may itself contain
  // a comma (e.g. "Visual C++ 2015, 2017", a thousands-separated bound) without
  // being mis-split. Scalar/within_last/empty stay strings. Legacy comma strings
  // are still tolerated on read.
  value: string | string[];
}

/** Normalize a stored list/range value (legacy comma string OR array) to an
 *  array of trimmed members. */
function toValueArray(value: string | string[]): string[] {
  if (Array.isArray(value)) return value.map((s) => String(s));
  return value ? value.split(',') : [];
}

interface FilterRowProps {
  fields: FieldCatalogEntry[];
  condition: FilterCondition;
  onChange: (condition: FilterCondition) => void;
  onRemove: () => void;
}

/** Build a fresh filter condition seeded with the first field's default op. */
export function emptyCondition(fields: FieldCatalogEntry[]): FilterCondition {
  const first = fields[0];
  if (!first) return { field_id: { kind: 'static', name: '' }, operator: '', value: '' };
  const specs = deriveOperatorSpecs(first);
  return { field_id: first.field_id, operator: specs[0]?.code ?? '', value: '' };
}

const CATEGORY_LABELS: Record<string, string> = {
  target: 'Asset Properties',
  advanced: 'Advanced',
  virtual: 'Computed',
  unified: 'Computed',
  extended_attributes: 'Data Source Specific',
  tags: 'Tags',
  enrichment: 'Enrichment',
  posture: 'Posture',
};

const CATEGORY_ORDER: Array<FieldCatalogEntry['category']> = [
  'target', 'advanced', 'virtual', 'extended_attributes', 'tags', 'enrichment', 'posture',
];
const FIELD_GROUP_ORDER = CATEGORY_ORDER.map((c) => CATEGORY_LABELS[c]);

// ── Chip input for arity:'list' operators ─────────────────────────────────
// Emits a string[] (not a comma-joined string), so a chip may contain a comma.
// Commit is on Enter/blur only — comma is NOT a separator key, otherwise a value
// like "Visual C++ 2015, 2017" could never be entered as one chip.
function ChipInput({ value, onChange }: { value: string | string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('');
  const chips = Array.from(new Set(toValueArray(value).map((s) => s.trim()).filter(Boolean)));

  function commitDraft() {
    const t = draft.trim();
    if (!t || chips.includes(t)) {
      setDraft('');
      return;
    }
    onChange([...chips, t]);
    setDraft('');
  }

  function removeChip(chip: string) {
    onChange(chips.filter((c) => c !== chip));
  }

  return (
    <div className={styles.chipInput}>
      {chips.map((chip) => (
        <span key={chip} className={styles.chip}>
          <span className={styles.chipText}>{chip}</span>
          <button
            type="button"
            className={styles.chipRemove}
            aria-label={`Remove ${chip}`}
            onClick={() => removeChip(chip)}
          >
            ×
          </button>
        </span>
      ))}
      <input
        className={styles.chipEntry}
        type="text"
        value={draft}
        placeholder="Add value…"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commitDraft();
          }
        }}
        onBlur={commitDraft}
      />
    </div>
  );
}

// ── between: two inputs serialized to a [lo, hi] array ────────────────────
// Emitting a real array (not "lo,hi") means a thousands-separated bound like
// "1,000" can't corrupt the range — the backend receives the bounds verbatim.
function BetweenInputs(
  { value, onChange, inputType }:
  { value: string | string[]; onChange: (v: string[] | string) => void; inputType: 'text' | 'number' | 'date' },
) {
  // Fully controlled (no local state): the bounds are derived from `value` every
  // render, and a partial range is retained IN the value as [lo, ''] / ['', hi]
  // so the inputs round-trip while typing. Completeness is NOT enforced here —
  // the readiness gate (isFilterValueReady) treats a half-filled range as
  // not-ready. Statelessness makes it immune to the index-keyed row-reuse desync.
  const parts = toValueArray(value);
  const lo = parts[0] ?? '';
  const hi = parts[1] ?? '';
  const emit = (nextLo: string, nextHi: string) =>
    onChange(nextLo || nextHi ? [nextLo, nextHi] : '');
  return (
    <div className={styles.rangeGroup}>
      <input
        className={styles.input}
        type={inputType}
        value={lo}
        onChange={(e) => emit(e.target.value, hi)}
        placeholder="From"
        aria-label="From"
      />
      <span className={styles.rangeSep}>and</span>
      <input
        className={styles.input}
        type={inputType}
        value={hi}
        onChange={(e) => emit(lo, e.target.value)}
        placeholder="To"
        aria-label="To"
      />
    </div>
  );
}

// Map a field's data_type to the HTML input type for its value controls (date
// picker for dates, numeric stepper for numbers, else free text) — D-2.
function inputTypeForDataType(dataType: string | undefined): 'text' | 'number' | 'date' {
  if (dataType === 'date' || dataType === 'timestamp') return 'date';
  if (dataType === 'integer' || dataType === 'float') return 'number';
  return 'text';
}

// ── within_last: amount + unit select serialized to "<n><unit>" (e.g. 30d) ──
// Units pinned to the backend resolver's set (RELATIVE_SECONDS_PER_UNIT in
// src/modules/fields/duration → operators.py: s/m/h/d/w).
const WITHIN_LAST_UNITS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 's', label: 'seconds' },
  { value: 'm', label: 'minutes' },
  { value: 'h', label: 'hours' },
  { value: 'd', label: 'days' },
  { value: 'w', label: 'weeks' },
];

function parseWithinLast(v: string): { amount: string; unit: string } {
  // Lenient: case-insensitive, and extract a leading amount even if the unit is
  // out of set (e.g. "90D" -> 90/d, "6mo" -> 6/m) so a stored value never resets
  // both fields to empty/default (D-4).
  const s = String(v ?? '').trim().toLowerCase();
  const m = /^(\d*)\s*([smhdw])?/.exec(s);
  return { amount: m?.[1] ?? '', unit: m?.[2] || 'd' };
}

function WithinLastInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { amount, unit } = parseWithinLast(value);
  // Fully controlled: always serialize "<amount><unit>" (so clearing the amount
  // keeps the chosen unit, e.g. "" + "w" -> "w", instead of resetting to days).
  // An amount-less value is not-ready per isFilterValueReady, so it is never
  // saved; it just preserves the unit selection while editing.
  const emit = (nextAmount: string, nextUnit: string) =>
    onChange(nextAmount || nextUnit ? `${nextAmount}${nextUnit}` : '');
  return (
    <div className={styles.rangeGroup}>
      <input
        className={styles.input}
        type="number"
        min="0"
        value={amount}
        onChange={(e) => emit(e.target.value, unit)}
        placeholder="N"
        aria-label="Amount"
      />
      <select
        className={styles.select}
        value={unit}
        onChange={(e) => emit(amount, e.target.value)}
        aria-label="Unit"
      >
        {WITHIN_LAST_UNITS.map((u) => (
          <option key={u.value} value={u.value}>{u.label}</option>
        ))}
      </select>
    </div>
  );
}

// The value SHAPE differs per operator family: scalar string, list (string[]),
// between ([lo,hi]), within_last ("<n><unit>"), or none. Switching across shapes
// must clear the stale value, otherwise an incompatible value rides on the new
// operator — e.g. an `any` array stranded on `eq` would be sent as `col = ARRAY[…]`
// (the scalar input shows empty but condition.value keeps the array).
function valueShape(code: string | undefined, arity: string | undefined): string {
  if (arity === 'none') return 'none';
  if (code === 'between') return 'between';
  if (code === 'within_last') return 'within_last';
  if (arity === 'list') return 'list';
  return 'scalar';
}

function shouldClearValue(
  prevCode: string | undefined,
  prevArity: string | undefined,
  nextCode: string,
  nextArity: string,
): boolean {
  return valueShape(prevCode, prevArity) !== valueShape(nextCode, nextArity);
}

export function FilterRow({ fields, condition, onChange, onRemove }: FilterRowProps) {
  const selectedEncoded = encodeFieldId(condition.field_id);
  const selectedEntry = useMemo(
    () => fields.find((f) => f.encoded_key === selectedEncoded),
    [fields, selectedEncoded],
  );
  const specs = useMemo(() => deriveOperatorSpecs(selectedEntry), [selectedEntry]);
  const currentSpec = specFor(condition.operator, specs);
  const arity = currentSpec?.arity ?? 'one';
  // The scalar text + within_last controls only ever hold a string; a list/range
  // value (array) is rendered by ChipInput/BetweenInputs instead.
  const scalarValue = typeof condition.value === 'string' ? condition.value : '';
  // Type-aware input: date picker for date/timestamp fields, numeric stepper for
  // numbers, else free text (D-2). Keyed on the FIELD's data_type, not the
  // operator's value_type (between's value_type is 'number' even on a date field).
  const valueInputType = inputTypeForDataType(selectedEntry?.data_type);

  const fieldOptions: DropdownOption[] = useMemo(
    () =>
      fields.map((f) => ({
        value: f.encoded_key,
        label: f.display_name,
        group: CATEGORY_LABELS[f.category ?? 'other'] ?? f.category ?? 'Other',
      })),
    [fields],
  );

  function handleFieldChange(encoded: string) {
    const entry = fields.find((f) => f.encoded_key === encoded);
    if (!entry) return;
    const nextSpecs = deriveOperatorSpecs(entry);
    // Preserve the operator if the new field still supports its code; else
    // default to the new field's first (canonical default) operator.
    const keep = nextSpecs.find((s) => s.code === condition.operator);
    const nextOp = keep ? keep.code : nextSpecs[0]?.code ?? '';
    const nextArity = (keep ?? nextSpecs[0])?.arity ?? 'one';
    onChange({
      ...condition,
      field_id: entry.field_id,
      operator: nextOp,
      value: shouldClearValue(currentSpec?.code, currentSpec?.arity, nextOp, nextArity)
        ? '' : condition.value,
    });
  }

  function handleOperatorChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const nextOp = e.target.value;
    const nextArity = specFor(nextOp, specs)?.arity ?? 'one';
    onChange({
      ...condition,
      operator: nextOp,
      value: shouldClearValue(currentSpec?.code, currentSpec?.arity, nextOp, nextArity)
        ? '' : condition.value,
    });
  }

  return (
    <div className={styles.row}>
      <SearchableDropdown
        value={selectedEncoded}
        options={fieldOptions}
        groupOrder={FIELD_GROUP_ORDER}
        onChange={handleFieldChange}
        ariaLabel="Field"
        placeholder="Select field…"
        searchPlaceholder="Search fields…"
      />

      <select
        className={styles.select}
        aria-label="Operator"
        value={currentSpec?.code ?? condition.operator}
        onChange={handleOperatorChange}
      >
        {specs.map((s) => (
          <option key={s.code} value={s.code}>
            {s.label}
          </option>
        ))}
      </select>

      {/* Value control: the two scalar-helper operators have bespoke widgets
          keyed on their code (they share arity 'list'/'one' with the generic
          controls, so code must take precedence); everything else falls back
          to the arity-driven control. */}
      {currentSpec?.code === 'between' ? (
        <BetweenInputs
          value={condition.value}
          inputType={valueInputType}
          onChange={(v) => onChange({ ...condition, value: v })}
        />
      ) : currentSpec?.code === 'within_last' ? (
        <WithinLastInput
          value={scalarValue}
          onChange={(v) => onChange({ ...condition, value: v })}
        />
      ) : arity === 'one' ? (
        <input
          className={styles.input}
          type={valueInputType}
          value={scalarValue}
          onChange={(e) => onChange({ ...condition, value: e.target.value })}
          placeholder="Value"
        />
      ) : arity === 'list' ? (
        <ChipInput
          value={condition.value}
          onChange={(v) => onChange({ ...condition, value: v })}
        />
      ) : null}

      <button className={styles.removeBtn} onClick={onRemove} type="button" aria-label="Remove filter">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
      </button>
    </div>
  );
}

export type { FilterCondition, FilterRowProps };
