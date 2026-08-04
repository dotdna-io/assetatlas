// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog/ConfirmDialog';
import { FilterRow, emptyCondition, isFilterValueReady } from '@/components/ui/FilterRow';
import { SearchableDropdown, type DropdownOption } from '@/components/ui/SearchableDropdown';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import {
  useCreatePostureSubRule,
  useUpdatePostureSubRule,
  usePreviewPostureSubRule,
} from '@/api/posture-rules';
import type {
  PostureSubRule,
  FilterCondition,
  ComplianceCriteria,
  PostureOutputMode,
} from '@/api/posture-rules';
import { useTagRuleFields } from '@/api/tag-rules';
import type { FieldCatalogEntry, FieldId } from '@/types/field-id';
import { encodeFieldId } from '@/types/field-id';
import ruleStyles from '../../rules/rules.module.css';

// Field-category → group label mapping mirrors the rules wizard's
// FilterRow so the dropdown grouping looks identical across both
// wizards. Same source of truth, same labels, same order.
const CATEGORY_LABELS: Record<string, string> = {
  target: 'Asset Properties',
  advanced: 'Advanced',
  virtual: 'Computed',
  extended_attributes: 'Data Source Specific',
  tags: 'Tags',
  enrichment: 'Enrichment',
  posture: 'Posture',
};
const FIELD_GROUP_ORDER = [
  'Asset Properties',
  'Advanced',
  'Computed',
  'Data Source Specific',
  'Tags',
  'Enrichment',
  'Posture',
];

function isRowReady(row: FilterCondition): boolean {
  return isFilterValueReady(row.operator, row.value);
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

interface WizardStepProps {
  stepIndex: number;
  activeIndex: number;
  title: string;
  summary?: ReactNode;
  children?: ReactNode;
}

function WizardStep({ stepIndex, activeIndex, title, summary, children }: WizardStepProps) {
  const isDone = stepIndex < activeIndex;
  const isActive = stepIndex === activeIndex;
  const isPending = stepIndex > activeIndex;

  const stepClass = [
    ruleStyles.wizCol,
    isDone ? ruleStyles.wizColDone : '',
    isActive ? ruleStyles.wizColActive : '',
    isPending ? ruleStyles.wizColPending : '',
  ].filter(Boolean).join(' ');

  const dotClass = [
    ruleStyles.wizColDot,
    isDone ? ruleStyles.wizDotDone : '',
    isActive ? ruleStyles.wizDotActive : '',
    isPending ? ruleStyles.wizDotPending : '',
  ].filter(Boolean).join(' ');

  const labelClass = [
    ruleStyles.wizColLabel,
    isDone ? ruleStyles.wizLabelDone : '',
    isActive ? ruleStyles.wizLabelActive : '',
    isPending ? ruleStyles.wizLabelPending : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={stepClass}>
      <div className={ruleStyles.wizColHeader}>
        <div className={dotClass}>
          {isDone ? <CheckIcon /> : stepIndex + 1}
        </div>
        <span className={labelClass}>{title}</span>
      </div>
      {isDone && summary && <div className={ruleStyles.wizColSummary}>{summary}</div>}
      {isActive && <div className={ruleStyles.wizColBody}>{children}</div>}
    </div>
  );
}

interface Props {
  isOpen: boolean;
  categoryId: string;
  existing?: PostureSubRule;
  onClose: () => void;
}

export function PostureSubRuleWizard({ isOpen, categoryId, existing, onClose }: Props) {
  const [status, setStatus] = useInlineStatus();
  const { data: fieldsData } = useTagRuleFields();

  // Flat field list — FilterRow handles its own grouping internally
  // via the entry's `category`, mirroring the rules wizard exactly.
  const fields: FieldCatalogEntry[] = useMemo(
    () => fieldsData?.entries ?? [],
    [fieldsData],
  );

  // Output field picker — same DropdownOption shape FilterRow uses,
  // so the dropdown looks identical to the field picker inside a
  // filter row.
  const outputFieldOptions: DropdownOption[] = useMemo(
    () =>
      fields.map((f) => ({
        value: f.encoded_key,
        label: f.display_name,
        group: CATEGORY_LABELS[f.category ?? 'other'] ?? f.category ?? 'Other',
      })),
    [fields],
  );

  const fieldByKey = useMemo(() => {
    const m = new Map<string, FieldCatalogEntry>();
    for (const f of fields) m.set(f.encoded_key, f);
    return m;
  }, [fields]);

  const [activeIndex, setActiveIndex] = useState(0);
  const [name, setName] = useState(existing?.name ?? '');
  const [description, setDescription] = useState(existing?.description ?? '');
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [targetFilters, setTargetFilters] = useState<FilterCondition[]>(
    existing?.target_filters ?? [],
  );
  const [criteria, setCriteria] = useState<ComplianceCriteria>(
    existing?.compliance_criteria ?? { logic: 'any', conditions: [] },
  );
  const [outputMode, setOutputMode] = useState<PostureOutputMode | null>(existing?.output_mode ?? null);
  const [outputFieldId, setOutputFieldId] = useState<FieldId | null>(
    existing?.output_value?.mode === 'field' ? existing.output_value.field_id : null,
  );
  const [outputText, setOutputText] = useState<string>(
    existing?.output_value?.mode === 'text' ? existing.output_value.text : '',
  );
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  function resetForm() {
    setActiveIndex(0);
    setName(existing?.name ?? '');
    setDescription(existing?.description ?? '');
    setEnabled(existing?.enabled ?? true);
    setTargetFilters(existing?.target_filters ?? []);
    setCriteria(
      existing?.compliance_criteria ?? { logic: 'any', conditions: [] },
    );
    setOutputMode(existing?.output_mode ?? null);
    setOutputFieldId(
      existing?.output_value?.mode === 'field' ? existing.output_value.field_id : null,
    );
    setOutputText(
      existing?.output_value?.mode === 'text' ? existing.output_value.text : '',
    );
    setShowCancelConfirm(false);
  }

  useEffect(() => {
    if (isOpen) resetForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- resetForm reads existing, reset on open/existing change
  }, [isOpen, existing]);

  // Scroll the wizard into view when it opens. The wizard is rendered at
  // the bottom of the (often long) category card, so when the user clicks
  // "New Rule" or an Edit icon further up the rule list, the form would
  // otherwise expand off-screen. Brief delay lets the open animation start
  // so the browser scrolls relative to the rendered (post-transition) box.
  const wizardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!isOpen) return;
    const t = setTimeout(() => {
      wizardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 80);
    return () => clearTimeout(t);
  }, [isOpen]);

  const createMutation = useCreatePostureSubRule();
  const updateMutation = useUpdatePostureSubRule();
  const previewMutation = usePreviewPostureSubRule();

  // Debounced preview — fire when step 3 (criteria) filters/criteria change
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Target match count — fires on the Targets step (index 1) when filters change
  const [targetMatchCount, setTargetMatchCount] = useState<number | null>(null);
  const [targetMatchPending, setTargetMatchPending] = useState(false);
  const targetDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (activeIndex !== 1) return;
    const validTargets = targetFilters.filter(isRowReady);
    if (validTargets.length === 0) {
      setTargetMatchCount(null);
      return;
    }
    setTargetMatchPending(true);
    if (targetDebounceRef.current) clearTimeout(targetDebounceRef.current);
    targetDebounceRef.current = setTimeout(() => {
      // Preview needs compliance_criteria — pass a dummy condition so the
      // endpoint validates. We only read the `matched` field (target-only).
      previewMutation.mutate(
        {
          target_filters: validTargets,
          compliance_criteria: { logic: 'any', conditions: validTargets },
        },
        {
          onSuccess: (data) => { setTargetMatchCount(data.matched); setTargetMatchPending(false); },
          onError: () => setTargetMatchPending(false),
        },
      );
    }, 600);
    return () => {
      if (targetDebounceRef.current) clearTimeout(targetDebounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- previewMutation.mutate is stable
  }, [activeIndex, targetFilters]);

  // Full preview — fires on the Criteria step (index 2)
  useEffect(() => {
    if (activeIndex !== 2) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const validTargets = targetFilters.filter(isRowReady);
      const validCriteria = criteria.conditions.filter(isRowReady);
      // Endpoint requires ≥1 target filter AND ≥1 criteria condition (Pydantic
      // min_items on both). Skip the preview until the user has filled one of
      // each, otherwise the half-filled state spams 400s.
      if (validTargets.length === 0 || validCriteria.length === 0) return;
      previewMutation.mutate({
        target_filters: validTargets,
        compliance_criteria: { logic: criteria.logic, conditions: validCriteria },
      });
    }, 600);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- previewMutation.mutate is stable
  }, [activeIndex, targetFilters, criteria]);

  const isEditing = !!existing;
  const isSaving = createMutation.isPending || updateMutation.isPending;

  const filledFilterCount = targetFilters.filter(isRowReady).length;
  const filledCriteriaCount = criteria.conditions.filter(isRowReady).length;

  function handleBack() {
    if (activeIndex > 0) setActiveIndex((i) => i - 1);
  }

  function handleContinue() {
    if (activeIndex < 2) setActiveIndex((i) => i + 1);
  }

  function handleCancelRequest() {
    setShowCancelConfirm(true);
  }

  function handleCancelConfirm() {
    setShowCancelConfirm(false);
    resetForm();
    onClose();
  }

  function handleSave() {
    const hasOutput =
      (outputMode === 'field' && outputFieldId !== null) ||
      (outputMode === 'text' && outputText.trim().length > 0);
    const data = {
      name: name.trim(),
      description: description.trim() || null,
      enabled,
      target_filters: targetFilters,
      compliance_criteria: {
        logic: criteria.logic,
        conditions: criteria.conditions,
      },
      output_mode: hasOutput ? outputMode : null,
      output_field_id: hasOutput && outputMode === 'field' ? outputFieldId : null,
      output_text: hasOutput && outputMode === 'text' ? outputText.trim() : null,
    };

    if (isEditing) {
      updateMutation.mutate(
        { subRuleId: existing!.id, data },
        {
          onSuccess: () => {
            setStatus(null); // the wizard closes onto the updated rule
            resetForm();
            onClose();
          },
          onError: (err) => setStatus({ kind: 'error', message: err.message || 'Failed to update' }),
        },
      );
    } else {
      createMutation.mutate(
        { categoryId, data },
        {
          onSuccess: () => {
            setStatus(null); // the wizard closes onto the new rule
            resetForm();
            onClose();
          },
          onError: (err) => setStatus({ kind: 'error', message: err.message || 'Failed to create' }),
        },
      );
    }
  }

  function isContinueDisabled(): boolean {
    if (activeIndex === 0) return !name.trim();
    if (activeIndex === 1) return !targetFilters.some(isRowReady);
    if (activeIndex === 2) return !criteria.conditions.some(isRowReady);
    return false;
  }

  const continueDisabled = isContinueDisabled();
  const isLastStep = activeIndex === 2;

  const wizardClasses = [
    ruleStyles.wizard,
    isOpen ? ruleStyles.wizardOpen : ruleStyles.wizardClosed,
  ].filter(Boolean).join(' ');

  return (
    <>
      <div className={wizardClasses} ref={wizardRef}>
        <InlineStatus status={status} />
        <div className={ruleStyles.wizardColumns}>
          {/* Step 1: Name */}
          <WizardStep
            stepIndex={0}
            activeIndex={activeIndex}
            title="Name"
            summary={name}
          >
            <div className={ruleStyles.formGroup}>
              <label className={ruleStyles.formLabel} htmlFor="posture-rule-name">
                Rule Name
              </label>
              <input
                id="posture-rule-name"
                className={ruleStyles.formInput}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Enter rule name"
                autoFocus
              />
            </div>

            <div className={ruleStyles.formGroup}>
              <label className={ruleStyles.formLabel} htmlFor="posture-rule-description">
                Description{' '}
                <span className={ruleStyles.formLabelOptional}>(optional)</span>
              </label>
              <textarea
                id="posture-rule-description"
                className={ruleStyles.formTextarea}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe what this rule does"
              />
            </div>

            <div className={ruleStyles.formGroup}>
              <label className={ruleStyles.formLabel}>
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  style={{ marginRight: 8 }}
                />
                Enabled
              </label>
            </div>
          </WizardStep>

          {/* Step 2: Targets */}
          <WizardStep
            stepIndex={1}
            activeIndex={activeIndex}
            title="Targets"
            summary={filledFilterCount > 0 ? `${filledFilterCount} filter${filledFilterCount !== 1 ? 's' : ''}` : undefined}
          >
            <div className={ruleStyles.filterSectionHeader}>Target Assets</div>

            {targetFilters.map((condition, i) => (
              <FilterRow
                key={i}
                fields={fields}
                condition={condition}
                onChange={(c) => setTargetFilters((prev) => prev.map((p, ix) => (ix === i ? c : p)))}
                onRemove={() => setTargetFilters((prev) => prev.filter((_, ix) => ix !== i))}
              />
            ))}

            <Button
              variant="secondary"
              size="sm"
              onClick={() => setTargetFilters((prev) => [...prev, emptyCondition(fields)])}
              className={ruleStyles.addFilterBtnSpaced}
            >
              + Add Filter
            </Button>

            {(targetMatchPending || targetMatchCount !== null) && (
              <div className={ruleStyles.previewCounter}>
                <span className={ruleStyles.previewNumber}>
                  {targetMatchPending ? '...' : (targetMatchCount ?? '--')}
                </span>
                <span className={ruleStyles.previewLabel}>assets match</span>
              </div>
            )}
          </WizardStep>

          {/* Step 3: Criteria */}
          <WizardStep
            stepIndex={2}
            activeIndex={activeIndex}
            title="Criteria"
            summary={filledCriteriaCount > 0 ? `${filledCriteriaCount} condition${filledCriteriaCount !== 1 ? 's' : ''}` : undefined}
          >
            <div className={ruleStyles.filterSectionHeader}>Compliance Criteria</div>

            <div className={ruleStyles.logicToggle}>
              <button
                type="button"
                className={[
                  ruleStyles.logicBtn,
                  ruleStyles.logicBtnLeft,
                  criteria.logic === 'all' ? ruleStyles.logicBtnActive : '',
                ].filter(Boolean).join(' ')}
                onClick={() => setCriteria((c) => ({ ...c, logic: 'all' }))}
              >
                ALL conditions
              </button>
              <button
                type="button"
                className={[
                  ruleStyles.logicBtn,
                  ruleStyles.logicBtnRight,
                  criteria.logic === 'any' ? ruleStyles.logicBtnActive : '',
                ].filter(Boolean).join(' ')}
                onClick={() => setCriteria((c) => ({ ...c, logic: 'any' }))}
              >
                ANY condition
              </button>
            </div>

            {criteria.conditions.map((condition, i) => (
              <FilterRow
                key={i}
                fields={fields}
                condition={condition}
                onChange={(c) =>
                  setCriteria((prev) => ({
                    ...prev,
                    conditions: prev.conditions.map((p, ix) => (ix === i ? c : p)),
                  }))
                }
                onRemove={() =>
                  setCriteria((prev) => ({
                    ...prev,
                    conditions: prev.conditions.filter((_, ix) => ix !== i),
                  }))
                }
              />
            ))}

            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                setCriteria((prev) => ({
                  ...prev,
                  conditions: [...prev.conditions, emptyCondition(fields)],
                }))
              }
              className={ruleStyles.addFilterBtnSpaced}
            >
              + Add Condition
            </Button>

            <div className={ruleStyles.formGroup} style={{ marginTop: 24 }}>
              <label className={ruleStyles.formLabel}>
                Rule Output{' '}
                <span className={ruleStyles.formLabelOptional}>(optional)</span>
              </label>
              <div className={ruleStyles.logicToggle}>
                <button
                  type="button"
                  className={[
                    ruleStyles.logicBtn,
                    ruleStyles.logicBtnLeft,
                    outputMode === null ? ruleStyles.logicBtnActive : '',
                  ].filter(Boolean).join(' ')}
                  onClick={() => { setOutputMode(null); setOutputFieldId(null); setOutputText(''); }}
                >
                  None
                </button>
                <button
                  type="button"
                  className={[
                    ruleStyles.logicBtn,
                    outputMode === 'field' ? ruleStyles.logicBtnActive : '',
                  ].filter(Boolean).join(' ')}
                  onClick={() => { setOutputMode('field'); setOutputFieldId(null); setOutputText(''); }}
                >
                  Field value
                </button>
                <button
                  type="button"
                  className={[
                    ruleStyles.logicBtn,
                    ruleStyles.logicBtnRight,
                    outputMode === 'text' ? ruleStyles.logicBtnActive : '',
                  ].filter(Boolean).join(' ')}
                  onClick={() => { setOutputMode('text'); setOutputFieldId(null); setOutputText(''); }}
                >
                  Text
                </button>
              </div>
              {outputMode === 'field' && (
                <div style={{ marginTop: 8 }}>
                  <SearchableDropdown
                    value={outputFieldId ? encodeFieldId(outputFieldId) : ''}
                    options={outputFieldOptions}
                    groupOrder={FIELD_GROUP_ORDER}
                    onChange={(key) => {
                      const entry = fieldByKey.get(key);
                      setOutputFieldId(entry ? entry.field_id : null);
                    }}
                    ariaLabel="Output field"
                    placeholder="Select field…"
                    searchPlaceholder="Search fields…"
                  />
                </div>
              )}
              {outputMode === 'text' && (
                <input
                  type="text"
                  className={ruleStyles.formInput}
                  placeholder="e.g. Active Directory"
                  value={outputText}
                  onChange={(e) => setOutputText(e.target.value)}
                  style={{ marginTop: 8, width: '40%' }}
                />
              )}
            </div>

            {/* Preview — counter style matches the rules wizard so the
                same visual language reads across both pages. */}
            {(previewMutation.isPending || previewMutation.data) && (
              <div className={ruleStyles.previewCounter}>
                <span className={ruleStyles.previewNumber}>
                  {previewMutation.isPending ? '...' : (previewMutation.data?.matched ?? '--')}
                </span>
                <span className={ruleStyles.previewLabel}>assets match</span>
                {!previewMutation.isPending && previewMutation.data && (
                  <div className={ruleStyles.previewCounts}>
                    <span className={ruleStyles.previewPass}>
                      pass: {previewMutation.data.pass}
                    </span>
                    <span className={ruleStyles.previewFail}>
                      fail: {previewMutation.data.fail}
                    </span>
                  </div>
                )}
              </div>
            )}
          </WizardStep>
        </div>

        <div className={ruleStyles.wizardFooter}>
          <div className={ruleStyles.wizardFooterLeft}>
            {activeIndex > 0 && (
              <Button variant="secondary" onClick={handleBack}>
                Back
              </Button>
            )}
            <Button variant="ghost" onClick={handleCancelRequest}>
              Cancel
            </Button>
          </div>
          {isLastStep ? (
            <Button
              variant="primary"
              onClick={handleSave}
              disabled={continueDisabled || isSaving}
              loading={isSaving}
            >
              {isEditing ? 'Save' : 'Create Rule'}
            </Button>
          ) : (
            <Button variant="primary" onClick={handleContinue} disabled={continueDisabled}>
              Continue
            </Button>
          )}
        </div>
      </div>

      <ConfirmDialog
        isOpen={showCancelConfirm}
        onConfirm={handleCancelConfirm}
        onCancel={() => setShowCancelConfirm(false)}
        title="Cancel rule creation?"
        message="Your progress will be lost."
        confirmLabel="Yes, cancel"
        cancelLabel="Keep editing"
        variant="warning"
      />
    </>
  );
}
