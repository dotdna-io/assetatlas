// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo, useRef, useEffect } from 'react';
import { Button } from '@/components/ui/Button';
import { DeleteIconButton } from '@/components/ui/IconButton';
import { getNestedValue } from '@/pages/integrations/integration-utils';
import type { WizardFormData, Transformation, TransformOperation } from './wizard-state';
import { applyTransformPipeline, generateOpId } from './transform-pipeline';
import styles from './TransformationsStep.module.css';

interface TransformationsStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
}

const OP_LABELS: Record<TransformOperation['type'], string> = {
  field: 'Field',
  static: 'Static text',
  current_timestamp: 'Current timestamp',
  trim: 'Trim',
  uppercase: 'Uppercase',
  lowercase: 'Lowercase',
  regex_extract: 'Regex extract',
  regex_replace: 'Regex replace',
  coalesce: 'Coalesce',
};

const NAME_RE = /^[a-z][a-z0-9_]*$/;

function validateName(name: string, existing: string[], sourceFields: string[], editingName?: string): string | null {
  if (!name) return 'Name is required';
  if (!NAME_RE.test(name)) return 'Must start with a-z, contain only a-z, 0-9, _';
  if (existing.includes(name) && name !== editingName) return 'Name already exists';
  if (sourceFields.includes(name)) return 'Conflicts with a source field name';
  return null;
}

function validateOperations(ops: TransformOperation[]): string | null {
  if (ops.length === 0) return 'At least one operation is required';
  for (const op of ops) {
    if (op.type === 'field' && !op.field) return 'Field operation needs a field path';
    if (op.type === 'coalesce' && (!op.fields || op.fields.length === 0)) return 'Coalesce needs at least one field';
    if (op.type === 'regex_extract' || op.type === 'regex_replace') {
      if (!op.pattern) return 'Regex operation requires a pattern';
      try { new RegExp(op.pattern); } catch { return `Invalid regex: ${op.pattern}`; }
    }
  }
  return null;
}

/* ─── Field search dropdown (reuses mapping panel pattern) ─── */

function FieldSearchDropdown({
  fields,
  sampleRecord,
  onSelect,
  onClose,
  multi,
  selected,
}: {
  fields: string[];
  sampleRecord?: Record<string, any>;
  onSelect: (field: string) => void;
  onClose: () => void;
  multi?: boolean;
  selected?: string[];
}) {
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCloseRef.current(); };
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onCloseRef.current();
    };
    document.addEventListener('keydown', handleKey);
    document.addEventListener('mousedown', handleClick);
    return () => {
      document.removeEventListener('keydown', handleKey);
      document.removeEventListener('mousedown', handleClick);
    };
  }, []);

  const filtered = useMemo(() => {
    if (!query) return fields;
    const q = query.toLowerCase();
    return fields.filter(f => f.toLowerCase().includes(q));
  }, [fields, query]);

  return (
    <div ref={ref} className={styles.fieldDropdown}>
      <input
        ref={inputRef}
        type="text"
        className={styles.fieldDropdownSearch}
        placeholder="Search fields..."
        value={query}
        onChange={e => setQuery(e.target.value)}
      />
      <div className={styles.fieldDropdownList}>
        {filtered.length === 0 && (
          <div className={styles.fieldDropdownEmpty}>No fields match</div>
        )}
        {filtered.map(f => {
          const isSelected = multi && selected?.includes(f);
          const sample = sampleRecord ? getNestedValue(sampleRecord, f) : undefined;
          return (
            <button
              key={f}
              type="button"
              className={`${styles.fieldDropdownItem} ${isSelected ? styles.fieldDropdownItemActive : ''}`}
              onClick={() => onSelect(f)}
            >
              <span className={styles.fieldDropdownItemName}>{f}</span>
              {sample != null && (
                <span className={styles.fieldDropdownItemSample}>
                  {String(sample).slice(0, 40)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Operation editor row ─── */

function OperationRow({
  op,
  index,
  total,
  sourceFields,
  sampleRecord,
  onChange,
  onRemove,
  onMove,
}: {
  op: TransformOperation;
  index: number;
  total: number;
  sourceFields: string[];
  sampleRecord?: Record<string, any>;
  onChange: (patch: Partial<TransformOperation>) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
}) {
  const [fieldDropdownOpen, setFieldDropdownOpen] = useState(false);
  const [coalesceDropdownOpen, setCoalesceDropdownOpen] = useState(false);

  return (
    <div className={styles.opRow}>
      <div className={styles.opRowHeader}>
        <span className={styles.opIndex}>{index + 1}</span>
        <span className={styles.opType}>{OP_LABELS[op.type]}</span>
        <div className={styles.opActions}>
          <button type="button" className={styles.opMoveBtn} disabled={index === 0} onClick={() => onMove(-1)} title="Move up">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 15l-6-6-6 6"/></svg>
          </button>
          <button type="button" className={styles.opMoveBtn} disabled={index === total - 1} onClick={() => onMove(1)} title="Move down">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6"/></svg>
          </button>
          <button type="button" className={styles.opRemoveBtn} onClick={onRemove} title="Remove">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
          </button>
        </div>
      </div>

      <div className={styles.opBody}>
        {op.type === 'field' && (
          <div className={styles.opField}>
            <div
              className={styles.opFieldSelector}
              onClick={() => setFieldDropdownOpen(!fieldDropdownOpen)}
            >
              {op.field || <span className={styles.opPlaceholder}>Select field...</span>}
            </div>
            {fieldDropdownOpen && (
              <FieldSearchDropdown
                fields={sourceFields}
                sampleRecord={sampleRecord}
                onSelect={f => { onChange({ field: f }); setFieldDropdownOpen(false); }}
                onClose={() => setFieldDropdownOpen(false)}
              />
            )}
          </div>
        )}

        {op.type === 'static' && (
          <input
            type="text"
            className={styles.opInput}
            placeholder="Enter text..."
            value={op.value ?? ''}
            onChange={e => onChange({ value: e.target.value })}
          />
        )}

        {op.type === 'regex_extract' && (
          <div className={styles.opGrid}>
            <div>
              <label className={styles.opLabel}>Pattern</label>
              <input
                type="text"
                className={styles.opInput}
                placeholder="e.g. (\\d+\\.\\d+)"
                value={op.pattern ?? ''}
                onChange={e => onChange({ pattern: e.target.value })}
              />
            </div>
            <div>
              <label className={styles.opLabel}>Group</label>
              <input
                type="number"
                className={styles.opInput}
                min={0}
                value={op.group ?? 0}
                onChange={e => onChange({ group: parseInt(e.target.value) || 0 })}
              />
            </div>
          </div>
        )}

        {op.type === 'regex_replace' && (
          <div className={styles.opGrid}>
            <div>
              <label className={styles.opLabel}>Pattern</label>
              <input
                type="text"
                className={styles.opInput}
                placeholder="e.g. \\s+"
                value={op.pattern ?? ''}
                onChange={e => onChange({ pattern: e.target.value })}
              />
            </div>
            <div>
              <label className={styles.opLabel}>Replacement</label>
              <input
                type="text"
                className={styles.opInput}
                placeholder="e.g. -"
                value={op.replacement ?? ''}
                onChange={e => onChange({ replacement: e.target.value })}
              />
            </div>
          </div>
        )}

        {op.type === 'coalesce' && (
          <div className={styles.opCoalesce}>
            <div className={styles.opCoalesceFields}>
              {(op.fields ?? []).map((f, i) => (
                <span key={f} className={styles.opCoalesceChip}>
                  <span className={styles.opCoalesceChipText}>{f}</span>
                  <button
                    type="button"
                    className={styles.opCoalesceChipRemove}
                    onClick={() => onChange({ fields: (op.fields ?? []).filter((_, j) => j !== i) })}
                  >
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
                  </button>
                </span>
              ))}
              <button
                type="button"
                className={styles.opCoalesceAdd}
                onClick={() => setCoalesceDropdownOpen(!coalesceDropdownOpen)}
              >
                + Add field
              </button>
            </div>
            {coalesceDropdownOpen && (
              <FieldSearchDropdown
                fields={sourceFields.filter(f => !(op.fields ?? []).includes(f))}
                sampleRecord={sampleRecord}
                onSelect={f => {
                  onChange({ fields: [...(op.fields ?? []), f] });
                  setCoalesceDropdownOpen(false);
                }}
                onClose={() => setCoalesceDropdownOpen(false)}
                multi
                selected={op.fields}
              />
            )}
            <div className={styles.opCoalesceDefault}>
              <label className={styles.opLabel}>Default value</label>
              <input
                type="text"
                className={styles.opInput}
                placeholder="Fallback if all fields empty..."
                value={op.default ?? ''}
                onChange={e => onChange({ default: e.target.value })}
              />
            </div>
          </div>
        )}

        {/* trim, uppercase, lowercase, current_timestamp have no params */}
      </div>
    </div>
  );
}

/* ─── Builder modal ─── */

function TransformBuilder({
  initial,
  existingNames,
  sourceFields,
  sampleRecord,
  onSave,
  onCancel,
}: {
  initial?: Transformation;
  existingNames: string[];
  sourceFields: string[];
  sampleRecord?: Record<string, any>;
  onSave: (t: Transformation) => void;
  onCancel: () => void;
}) {
  const isEditing = !!initial;
  const [name, setName] = useState(initial?.name ?? '');
  const [operations, setOperations] = useState<TransformOperation[]>(
    initial?.operations ?? [],
  );
  const [addOpType, setAddOpType] = useState<TransformOperation['type']>('field');
  const overlayRef = useRef<HTMLDivElement>(null);

  const nameError = validateName(name, existingNames, sourceFields, initial?.name);
  const opsError = validateOperations(operations);

  const preview = useMemo(() => {
    if (!sampleRecord || operations.length === 0) return null;
    return applyTransformPipeline(operations, sampleRecord);
  }, [operations, sampleRecord]);

  const updateOp = (index: number, patch: Partial<TransformOperation>) => {
    setOperations(ops => ops.map((o, i) => i === index ? { ...o, ...patch } : o));
  };
  const removeOp = (index: number) => {
    setOperations(ops => ops.filter((_, i) => i !== index));
  };
  const moveOp = (index: number, dir: -1 | 1) => {
    setOperations(ops => {
      const next = [...ops];
      const target = index + dir;
      if (target < 0 || target >= next.length) return next;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };
  const addOp = () => {
    const newOp: TransformOperation = { id: generateOpId(), type: addOpType };
    if (addOpType === 'regex_extract') newOp.group = 0;
    if (addOpType === 'coalesce') { newOp.fields = []; newOp.default = ''; }
    if (addOpType === 'regex_replace') newOp.replacement = '';
    setOperations(ops => [...ops, newOp]);
  };

  const canSave = !nameError && !opsError;

  const handleSave = () => {
    if (!canSave) return;
    onSave({ name, operations });
  };

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onCancel]);

  return (
    <div className={styles.overlay} ref={overlayRef} onClick={e => { if (e.target === overlayRef.current) onCancel(); }}>
      <div className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="transform-builder-title">
        <div className={styles.modalHeader}>
          <span id="transform-builder-title" className={styles.modalTitle}>
            {isEditing ? 'Edit Transformation' : 'Create Transformation'}
          </span>
          <button type="button" className={styles.modalClose} onClick={onCancel}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
          </button>
        </div>

        <div className={styles.modalBody}>
          {/* Left: builder */}
          <div className={styles.builderPanel}>
            <div className={styles.nameField}>
              <label className={styles.opLabel}>Virtual field name</label>
              <input
                type="text"
                className={`${styles.opInput} ${nameError && name ? styles.opInputError : ''}`}
                placeholder="e.g. full_os"
                value={name}
                onChange={e => setName(e.target.value)}
                disabled={isEditing}
              />
              {nameError && name && <div className={styles.errorText}>{nameError}</div>}
            </div>

            <div className={styles.opsLabel}>Operations</div>

            {operations.length === 0 && (
              <div className={styles.opsEmpty}>
                Add operations to build the transformation pipeline
              </div>
            )}

            <div className={styles.opsList}>
              {operations.map((op, i) => (
                <OperationRow
                  key={op.id}
                  op={op}
                  index={i}
                  total={operations.length}
                  sourceFields={sourceFields}
                  sampleRecord={sampleRecord}
                  onChange={patch => updateOp(i, patch)}
                  onRemove={() => removeOp(i)}
                  onMove={dir => moveOp(i, dir)}
                />
              ))}
            </div>

            <div className={styles.addOpRow}>
              <select
                className={styles.addOpSelect}
                value={addOpType}
                onChange={e => setAddOpType(e.target.value as TransformOperation['type'])}
              >
                <option value="field">Field</option>
                <option value="static">Static text</option>
                <option value="current_timestamp">Current timestamp</option>
                <option value="trim">Trim</option>
                <option value="uppercase">Uppercase</option>
                <option value="lowercase">Lowercase</option>
                <option value="regex_extract">Regex extract</option>
                <option value="regex_replace">Regex replace</option>
                <option value="coalesce">Coalesce</option>
              </select>
              <Button variant="secondary" size="sm" onClick={addOp}>+ Add</Button>
            </div>
          </div>

          {/* Right: live preview */}
          <div className={styles.previewPanel}>
            <div className={styles.previewTitle}>Live Preview</div>
            {!sampleRecord ? (
              <div className={styles.previewEmpty}>
                Test connection first to see live preview
              </div>
            ) : !preview || preview.steps.length === 0 ? (
              <div className={styles.previewEmpty}>
                Add operations to see preview
              </div>
            ) : (
              <>
                <div className={styles.previewSteps}>
                  {preview.steps.map((step, i) => (
                    <div key={i} className={styles.previewStep}>
                      <div className={styles.previewStepHeader}>
                        <span className={styles.previewStepNum}>{i + 1}</span>
                        <span className={styles.previewStepDesc}>{step.description}</span>
                      </div>
                      <div className={styles.previewStepResult}>
                        {step.result || <span className={styles.previewStepEmpty}>(empty)</span>}
                      </div>
                    </div>
                  ))}
                </div>
                <div className={styles.previewFinal}>
                  <div className={styles.previewFinalLabel}>Result</div>
                  <div className={styles.previewFinalValue}>
                    {preview.finalResult ?? <span className={styles.previewStepEmpty}>(null)</span>}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        <div className={styles.modalFooter}>
          {opsError && operations.length > 0 && (
            <span className={styles.errorText}>{opsError}</span>
          )}
          <div className={styles.modalFooterButtons}>
            <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
            <Button variant="primary" size="sm" onClick={handleSave} disabled={!canSave}>
              {isEditing ? 'Save Changes' : 'Create'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── Main component ─── */

export function TransformationsStep({ formData, onUpdate }: TransformationsStepProps) {
  const transformations = formData.transformations;
  const sourceFields = formData.sourceFields ?? [];
  const firstSample = formData.sampleData?.[0];

  const [builderOpen, setBuilderOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);

  const existingNames = useMemo(
    () => transformations.map(t => t.name),
    [transformations],
  );

  const listPreviews = useMemo(
    () => transformations.map(t =>
      firstSample ? applyTransformPipeline(t.operations, firstSample) : null),
    [transformations, firstSample],
  );

  const handleSave = (t: Transformation) => {
    if (editingIndex !== null) {
      const updated = transformations.map((existing, i) =>
        i === editingIndex ? t : existing,
      );
      onUpdate({ transformations: updated });
    } else {
      onUpdate({ transformations: [...transformations, t] });
    }
    setBuilderOpen(false);
    setEditingIndex(null);
  };

  const handleEdit = (index: number) => {
    setEditingIndex(index);
    setBuilderOpen(true);
  };

  const handleDelete = (index: number) => {
    const deleted = transformations[index];
    const updated = transformations.filter((_, i) => i !== index);
    // Clean up any field_mapping entries that reference the deleted virtual field
    const cleanedMapping = formData.field_mapping.filter(m => m.source !== deleted.name);
    const cleanedPreserve = formData.preserve_fields.filter(f => f !== deleted.name);
    onUpdate({
      transformations: updated,
      field_mapping: cleanedMapping,
      preserve_fields: cleanedPreserve,
    });
  };

  const handleCreate = () => {
    setEditingIndex(null);
    setBuilderOpen(true);
  };

  return (
    <div className={styles.section}>
      <p className={styles.description}>
        Define computed virtual fields by chaining operations on source data.
        Virtual fields appear as mappable source fields below. This step is optional.
      </p>

      {transformations.length === 0 ? (
        <div className={styles.emptyState}>
          No transformations configured. Source fields will be used as-is.
        </div>
      ) : (
        <div className={styles.transformList}>
          {transformations.map((t, i) => (
              <div key={t.name} className={styles.transformCard}>
                <div className={styles.transformHeader}>
                  <div className={styles.transformName}>{t.name}</div>
                  <span className={styles.transformOpCount}>
                    {t.operations.length} op{t.operations.length !== 1 ? 's' : ''}
                  </span>
                  <div className={styles.transformActions}>
                    <button type="button" className={styles.transformEditBtn} onClick={() => handleEdit(i)} title="Edit">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </button>
                    <DeleteIconButton className={styles.transformDeleteBtn} onClick={() => handleDelete(i)} title="Delete" />
                  </div>
                </div>
                {listPreviews[i]?.finalResult != null && (
                  <div className={styles.transformPreview}>
                    {listPreviews[i]!.finalResult}
                  </div>
                )}
              </div>
            ))}
        </div>
      )}

      <div className={styles.addButton}>
        <Button variant="secondary" size="sm" onClick={handleCreate}>
          + Create Transformation
        </Button>
      </div>

      {builderOpen && (
        <TransformBuilder
          initial={editingIndex !== null ? transformations[editingIndex] : undefined}
          existingNames={existingNames}
          sourceFields={sourceFields}
          sampleRecord={firstSample}
          onSave={handleSave}
          onCancel={() => { setBuilderOpen(false); setEditingIndex(null); }}
        />
      )}
    </div>
  );
}
