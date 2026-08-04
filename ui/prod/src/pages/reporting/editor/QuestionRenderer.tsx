// SPDX-License-Identifier: AGPL-3.0-or-later
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { FilterRow, emptyCondition, normalizeOperator } from '@/components/ui/FilterRow';
import type { FilterCondition } from '@/components/ui/FilterRow';
import { Button } from '@/components/ui/Button';
import type { FieldCatalogEntry, FieldId, OperatorSpec } from '@/types/field-id';
import type { QuestionSection, QuestionField } from '../data/chartQuestions';
import styles from './QuestionRenderer.module.css';

/** Shape used by reporting pages. `value` is the encoded_key string (for
 *  <option value=>) and `field_id` is the typed FieldId for body construction. */
interface AssetField {
  name: string;
  value: string;
  field_id: FieldId;
  type: string;
  category: string;
}

interface QuestionRendererProps {
  sections: QuestionSection[];
  answers: Record<string, any>;
  chartMeta: Record<string, any>;
  assetFields: AssetField[];
  onAnswerChange: (key: string, value: any) => void;
}

// ── Field renderer ──────────────────────────────────────────────────────────

function renderField(
  field: QuestionField,
  value: any,
  assetFields: AssetField[],
  onAnswerChange: (key: string, value: any) => void,
) {
  switch (field.type) {
    case 'field-select': {
      const filtered = field.fieldTypes
        ? assetFields.filter((f) => field.fieldTypes!.includes(f.type))
        : assetFields;

      const grouped = filtered.reduce<Record<string, typeof assetFields>>(
        (acc, f) => {
          (acc[f.category] ??= []).push(f);
          return acc;
        },
        {},
      );

      return (
        <select
          className={styles.select}
          value={value ?? ''}
          onChange={(e) => onAnswerChange(field.id, e.target.value || undefined)}
        >
          <option value="">{field.placeholder ?? 'Select...'}</option>
          {Object.entries(grouped).map(([cat, fields]) => (
            <optgroup key={cat} label={cat}>
              {fields.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      );
    }

    case 'multi-field-select': {
      const filtered = field.fieldTypes
        ? assetFields.filter((f) => field.fieldTypes!.includes(f.type))
        : assetFields;

      const grouped = filtered.reduce<Record<string, typeof assetFields>>(
        (acc, f) => {
          (acc[f.category] ??= []).push(f);
          return acc;
        },
        {},
      );

      const selected: string[] = Array.isArray(value) ? value : [];

      const toggle = (name: string) => {
        const next = selected.includes(name)
          ? selected.filter((s) => s !== name)
          : [...selected, name];
        onAnswerChange(field.id, next.length > 0 ? next : undefined);
      };

      return (
        <div className={styles.checkboxGroup}>
          {field.placeholder && selected.length === 0 && (
            <span className={styles.checkboxHint}>{field.placeholder}</span>
          )}
          {Object.entries(grouped).map(([cat, fields]) => (
            <div key={cat}>
              <span className={styles.checkboxCategory}>{cat}</span>
              {fields.map((f) => (
                <label key={f.value} className={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={selected.includes(f.value)}
                    onChange={() => toggle(f.value)}
                  />
                  {f.name}
                </label>
              ))}
            </div>
          ))}
        </div>
      );
    }

    case 'choice':
      return (
        <select
          className={styles.select}
          value={value ?? field.default ?? ''}
          onChange={(e) => {
            const raw = e.target.value;
            const num = Number(raw);
            onAnswerChange(field.id, isNaN(num) ? raw : num);
          }}
        >
          {!field.default && <option value="">Select...</option>}
          {(field.options ?? []).map((opt) => (
            <option key={String(opt.value)} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      );

    case 'number':
      return (
        <input
          type="number"
          className={styles.input}
          // Don't fall back to field.default here — that snaps the value
          // back the moment the user clears the input. The form is
          // initialised with defaults in ChartEditor, so this just renders
          // what's currently in `answers`.
          value={value ?? ''}
          min={field.min}
          max={field.max}
          step={field.step ?? 1}
          placeholder={
            field.default !== undefined ? String(field.default) : undefined
          }
          onChange={(e) => {
            const v = e.target.value;
            onAnswerChange(field.id, v === '' ? undefined : Number(v));
          }}
        />
      );

    case 'text':
      return (
        <input
          type="text"
          className={styles.input}
          value={value ?? ''}
          placeholder={field.placeholder}
          onChange={(e) =>
            onAnswerChange(field.id, e.target.value || undefined)
          }
        />
      );

    case 'toggle':
      return (
        <ToggleSwitch
          checked={value ?? field.default ?? false}
          onChange={(v) => onAnswerChange(field.id, v)}
        />
      );

    case 'filter-builder': {
      const filters: FilterCondition[] = Array.isArray(value) ? value : [];
      // Adapt AssetField[] to FieldCatalogEntry[] that FilterRow expects.
      // We synthesize the minimum needed fields; downstream FilterRow only
      // uses display_name, field_id, encoded_key, and operator_specs.
      const filterFields: FieldCatalogEntry[] = assetFields.map((f) => ({
        display_name: f.name,
        field_id: f.field_id,
        encoded_key: f.value,
        category: 'target',
        data_type: f.type,
        operator_specs: getOperatorSpecsForType(f.type),
        is_array: f.type.endsWith('_array'),
        is_grouped: false,
        filter_only: false,
        member_sources: null,
      }));

      const handleFilterChange = (idx: number, condition: FilterCondition) => {
        const updated = filters.map((f, i) => (i === idx ? condition : f));
        onAnswerChange(field.id, updated.length > 0 ? updated : undefined);
      };

      const handleFilterRemove = (idx: number) => {
        const updated = filters.filter((_, i) => i !== idx);
        onAnswerChange(field.id, updated.length > 0 ? updated : undefined);
      };

      const handleFilterAdd = () => {
        onAnswerChange(field.id, [...filters, emptyCondition(filterFields)]);
      };

      return (
        <div>
          {filters.map((condition, idx) => (
            <FilterRow
              key={idx}
              fields={filterFields}
              condition={condition}
              onChange={(c) => handleFilterChange(idx, c)}
              onRemove={() => handleFilterRemove(idx)}
            />
          ))}
          <Button
            variant="secondary"
            size="sm"
            className={styles.addFilterBtn}
            onClick={handleFilterAdd}
          >
            + Add Filter
          </Button>
        </div>
      );
    }

    default:
      return null;
  }
}

/** Build an OperatorSpec with a canonical registry code + a human label. The
 *  label and its normalized form are also registered as aliases so a rule
 *  saved before the unification (which stored the spaced/underscored label,
 *  e.g. "not equals" / "not_equals") still resolves to this spec via specFor —
 *  the dropdown then shows the canonical option and re-saves the code. */
function spec(
  code: string,
  label: string,
  arity: OperatorSpec['arity'],
  value_type: OperatorSpec['value_type'] = 'text',
): OperatorSpec {
  const aliases = Array.from(new Set([label, normalizeOperator(label)]));
  return { code, label, symbol: null, aliases, arity, value_type };
}

/** operator_specs for a synthesized reporting field. Same operator SET as the
 *  old getOperatorsForType, now expressed with canonical codes so the reporting
 *  filter builder shares the FilterRow/operator_specs mechanism used by the
 *  tag/posture wizards. chartDataPipeline.OPERATOR_MAP maps these codes to the
 *  charts FilterOperator enum. */
function getOperatorSpecsForType(dataType: string): OperatorSpec[] {
  switch (dataType) {
    case 'string':
    case 'string_array':
      return [
        spec('eq', 'equals', 'one'),
        spec('neq', 'not equals', 'one'),
        spec('contains', 'contains', 'one'),
        spec('ncontains', 'not contains', 'one'),
        spec('starts', 'starts with', 'one'),
        spec('ends', 'ends with', 'one'),
        // Set membership (charts backend: IN / NOT_IN). `all` (has all of) is
        // intentionally omitted — the charts query builder has no ALL operator.
        spec('any', 'any of', 'list'),
        spec('none', 'none of', 'list'),
        spec('empty', 'is empty', 'none'),
        spec('notempty', 'is not empty', 'none'),
      ];
    case 'number':
    case 'float':
    case 'integer':
      return [
        spec('eq', 'equals', 'one', 'number'),
        spec('neq', 'not equals', 'one', 'number'),
        spec('gt', 'greater than', 'one', 'number'),
        spec('lt', 'less than', 'one', 'number'),
        spec('gte', 'greater than or equal', 'one', 'number'),
        spec('lte', 'less than or equal', 'one', 'number'),
        spec('between', 'between', 'list', 'number'),
      ];
    case 'date':
    case 'timestamp':
      // Date/timestamp fields previously fell to the text default. `within_last`
      // is omitted — the charts query builder has no WITHIN operator (it would
      // 422); between is a date range (charts BETWEEN).
      return [
        spec('eq', 'equals', 'one', 'date'),
        spec('neq', 'not equals', 'one', 'date'),
        spec('gte', 'on or after', 'one', 'date'),
        spec('lte', 'on or before', 'one', 'date'),
        spec('gt', 'after', 'one', 'date'),
        spec('lt', 'before', 'one', 'date'),
        spec('between', 'between', 'list', 'number'),
        spec('empty', 'is empty', 'none'),
        spec('notempty', 'is not empty', 'none'),
      ];
    case 'boolean':
      return [spec('eq', 'equals', 'one'), spec('neq', 'not equals', 'one')];
    default:
      return [
        spec('eq', 'equals', 'one'),
        spec('neq', 'not equals', 'one'),
        spec('contains', 'contains', 'one'),
      ];
  }
}

// ── Component ───────────────────────────────────────────────────────────────

export function QuestionRenderer({
  sections,
  answers,
  chartMeta,
  assetFields,
  onAnswerChange,
}: QuestionRendererProps) {
  const ctx = { answers, chartMeta };

  return (
    <div className={styles.form}>
      {sections.map((section) => {
        if (section.showIf && !section.showIf(ctx)) return null;
        return (
          <div key={section.title} className={styles.section}>
            <h4 className={styles.sectionTitle}>{section.title}</h4>
            {section.fields.map((field) => {
              if (field.showIf && !field.showIf(ctx)) return null;
              const fieldClasses = [
                styles.field,
                field.type === 'toggle' ? styles.toggleField : '',
              ].filter(Boolean).join(' ');
              return (
                <div key={field.id} className={fieldClasses}>
                  <label className={styles.fieldLabel}>{field.label}</label>
                  {renderField(field, answers[field.id], assetFields, onAnswerChange)}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
