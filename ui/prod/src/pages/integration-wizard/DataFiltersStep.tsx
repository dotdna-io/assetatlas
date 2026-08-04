// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { Button } from '@/components/ui/Button';
import { getNestedValue, inferFieldType } from '@/pages/integrations/integration-utils';
import type { WizardFormData, DataFilter } from './wizard-state';
import styles from './DataFiltersStep.module.css';

const OPERATORS_BY_TYPE: Record<string, { value: string; label: string }[]> = {
  string: [
    { value: 'equals', label: 'Equals' },
    { value: 'not_equals', label: 'Not Equals' },
    { value: 'contains', label: 'Contains' },
    { value: 'not_contains', label: 'Not Contains' },
    { value: 'starts_with', label: 'Starts With' },
    { value: 'ends_with', label: 'Ends With' },
    { value: 'is_empty', label: 'Is Empty' },
    { value: 'is_not_empty', label: 'Is Not Empty' },
  ],
  number: [
    { value: 'equals', label: 'Equals' },
    { value: 'not_equals', label: 'Not Equals' },
    { value: 'greater_than', label: 'Greater Than' },
    { value: 'less_than', label: 'Less Than' },
    { value: 'is_empty', label: 'Is Empty' },
    { value: 'is_not_empty', label: 'Is Not Empty' },
  ],
  boolean: [
    { value: 'is_true', label: 'Is True' },
    { value: 'is_false', label: 'Is False' },
  ],
  datetime: [
    { value: 'equals', label: 'Equals' },
    { value: 'greater_than', label: 'After' },
    { value: 'less_than', label: 'Before' },
    { value: 'is_empty', label: 'Is Empty' },
  ],
  ip_address: [
    { value: 'equals', label: 'Equals' },
    { value: 'not_equals', label: 'Not Equals' },
    { value: 'starts_with', label: 'Starts With' },
    { value: 'is_empty', label: 'Is Empty' },
    { value: 'is_not_empty', label: 'Is Not Empty' },
  ],
};

const VALUE_HIDDEN_OPERATORS = new Set(['is_empty', 'is_not_empty', 'is_true', 'is_false']);


// Exported for unit testing. Mirrors server-side ingest semantics
// (data_filters.evaluate_condition -> the operator registry's match()): string
// comparisons are CASE-INSENSITIVE, so the wizard's preview match count agrees
// with what is actually ingested (H-1).
export function evaluateFilter(record: Record<string, any>, filter: DataFilter): boolean {
  const rawValue = getNestedValue(record, filter.field);
  const op = filter.operator;

  if (op === 'is_empty') return rawValue == null || rawValue === '';
  if (op === 'is_not_empty') return rawValue != null && rawValue !== '';
  // Mirror server _m_is_true/_m_is_false: case-insensitive on the string form, so
  // an uppercase 'TRUE'/'True' source value previews the same as it ingests (H-1).
  if (op === 'is_true') {
    if (typeof rawValue === 'boolean') return rawValue;
    if (typeof rawValue === 'string') return rawValue.toLowerCase() === 'true';
    return Boolean(rawValue);
  }
  if (op === 'is_false') {
    if (typeof rawValue === 'boolean') return !rawValue;
    if (typeof rawValue === 'string') return rawValue.toLowerCase() === 'false';
    return !rawValue;
  }

  const strValue = String(rawValue ?? '').toLowerCase();
  const filterVal = String(filter.value ?? '').toLowerCase();

  if (op === 'equals') return strValue === filterVal;
  if (op === 'not_equals') return strValue !== filterVal;
  if (op === 'contains') return strValue.includes(filterVal);
  if (op === 'not_contains') return !strValue.includes(filterVal);
  if (op === 'starts_with') return strValue.startsWith(filterVal);
  if (op === 'ends_with') return strValue.endsWith(filterVal);

  const numValue = Number(rawValue);
  const numFilter = Number(filter.value);
  if (op === 'greater_than') return !isNaN(numValue) && !isNaN(numFilter) && numValue > numFilter;
  if (op === 'less_than') return !isNaN(numValue) && !isNaN(numFilter) && numValue < numFilter;

  return true;
}

function evaluateFiltersClient(
  sampleData: Record<string, any>[],
  filters: DataFilter[],
): { matchCount: number; totalCount: number } {
  const totalCount = sampleData.length;
  if (filters.length === 0) return { matchCount: totalCount, totalCount };

  const activeFilters = filters.filter((f) => f.field && f.operator);
  if (activeFilters.length === 0) return { matchCount: totalCount, totalCount };

  const matchCount = sampleData.filter((record) =>
    activeFilters.every((f) => evaluateFilter(record, f)),
  ).length;

  return { matchCount, totalCount };
}

interface DataFiltersStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
}

export function DataFiltersStep({ formData, onUpdate }: DataFiltersStepProps) {
  const filters = formData.data_filters;
  const sourceFields = formData.sourceFields ?? [];
  const sampleData = formData.sampleData ?? [];

  const fieldTypes = useMemo(() => {
    const types: Record<string, string> = {};
    if (!formData.sampleData?.length || !formData.sourceFields) return types;
    for (const field of formData.sourceFields) {
      const sample = getNestedValue(formData.sampleData[0], field);
      if (sample != null) types[field] = inferFieldType(sample);
    }
    return types;
  }, [formData.sampleData, formData.sourceFields]);

  function getOperatorsForField(field: string): { value: string; label: string }[] {
    const type = fieldTypes[field] || 'string';
    return OPERATORS_BY_TYPE[type] || OPERATORS_BY_TYPE.string;
  }

  const preview = useMemo(
    () => evaluateFiltersClient(sampleData, filters),
    [sampleData, filters],
  );

  const updateFilter = (index: number, patch: Partial<DataFilter>) => {
    const updated = filters.map((f, i) => (i === index ? { ...f, ...patch } : f));
    onUpdate({ data_filters: updated });
  };

  function handleFieldChange(index: number, newField: string) {
    const operators = getOperatorsForField(newField);
    updateFilter(index, {
      field: newField,
      operator: operators[0]?.value || 'equals',
      value: '',
    });
  }

  const addFilter = () => {
    const firstField = sourceFields[0] ?? '';
    const operators = getOperatorsForField(firstField);
    onUpdate({
      data_filters: [
        ...filters,
        { field: firstField, operator: operators[0]?.value || 'equals', value: '' },
      ],
    });
  };

  const removeFilter = (index: number) => {
    onUpdate({ data_filters: filters.filter((_, i) => i !== index) });
  };

  return (
    <div className={styles.section}>
      <p className={styles.description}>
        Define filters to include only matching records. All conditions must be met (AND logic).
        This step is optional — skip it to import all records.
      </p>

      {filters.length === 0 ? (
        <div className={styles.emptyState}>
          No filters configured. All records will be imported.
        </div>
      ) : (
        <div className={styles.filterList}>
          {filters.map((filter, i) => (
            <div key={i} className={styles.filterRow}>
              <select
                className={styles.filterField}
                value={filter.field}
                onChange={(e) => handleFieldChange(i, e.target.value)}
              >
                {sourceFields.length === 0 && (
                  <option value="">— no fields available —</option>
                )}
                {sourceFields.map((f) => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>

              <select
                className={styles.filterOp}
                value={filter.operator}
                onChange={(e) => updateFilter(i, { operator: e.target.value, value: '' })}
              >
                {getOperatorsForField(filter.field).map(op => (
                  <option key={op.value} value={op.value}>{op.label}</option>
                ))}
              </select>

              {!VALUE_HIDDEN_OPERATORS.has(filter.operator) && (
                <input
                  type="text"
                  className={styles.filterValue}
                  placeholder="Value"
                  value={filter.value ?? ''}
                  onChange={(e) => updateFilter(i, { value: e.target.value })}
                />
              )}

              <button
                type="button"
                className={styles.removeButton}
                onClick={() => removeFilter(i)}
                title="Remove filter"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
              </button>
            </div>
          ))}
        </div>
      )}

      <div className={styles.addButton}>
        <Button variant="secondary" size="sm" onClick={addFilter}>
          + Add Filter
        </Button>
      </div>

      {sampleData.length > 0 && (
        <div className={styles.preview}>
          <span className={styles.matchCount}>{preview.matchCount} of {preview.totalCount}</span>
          {' '}sample records match
          {preview.totalCount > 0 && (
            <span> ({Math.round((preview.matchCount / preview.totalCount) * 100)}%)</span>
          )}
        </div>
      )}
    </div>
  );
}
