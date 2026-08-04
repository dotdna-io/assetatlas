// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog/ConfirmDialog';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { TypeSelector } from './wizard/TypeSelector';
import type { RuleType } from './wizard/TypeSelector';
import { NameStep } from './wizard/NameStep';
import { TargetFilters } from './wizard/TargetFilters';
import { TagsStep } from './wizard/TagsStep';
import { AlertSettingsStep } from './wizard/AlertSettingsStep';
import type { AlertSettings } from './wizard/AlertSettingsStep';
import { useCreateTagRule } from '@/api/tag-rules';
import { isFilterValueReady } from '@/components/ui/FilterRow';
import type { FilterCondition as UIFilterCondition } from '@/components/ui/FilterRow';
import { useCreateAlertRule } from '@/api/alerts';
import type { CreateAlertRulePayload } from '@/api/alerts';
import type { TagRuleCondition, ComplianceCriteria, TagRuleTags } from '@/api/tag-rules';
import { encodeFieldId } from '@/types/field-id';
import styles from './rules.module.css';

interface RuleWizardProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: () => void;
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

interface StepDef {
  key: string;
  title: string;
  summary: ReactNode;
  content: ReactNode;
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
    styles.wizCol,
    isDone ? styles.wizColDone : '',
    isActive ? styles.wizColActive : '',
    isPending ? styles.wizColPending : '',
  ].filter(Boolean).join(' ');

  const dotClass = [
    styles.wizColDot,
    isDone ? styles.wizDotDone : '',
    isActive ? styles.wizDotActive : '',
    isPending ? styles.wizDotPending : '',
  ].filter(Boolean).join(' ');

  const labelClass = [
    styles.wizColLabel,
    isDone ? styles.wizLabelDone : '',
    isActive ? styles.wizLabelActive : '',
    isPending ? styles.wizLabelPending : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={stepClass}>
      <div className={styles.wizColHeader}>
        <div className={dotClass}>
          {isDone ? <CheckIcon /> : stepIndex + 1}
        </div>
        <span className={labelClass}>{title}</span>
      </div>
      {isDone && summary && <div className={styles.wizColSummary}>{summary}</div>}
      {isActive && <div className={styles.wizColBody}>{children}</div>}
    </div>
  );
}

export function RuleWizard({ isOpen, onClose, onCreated }: RuleWizardProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [ruleType, setRuleType] = useState<RuleType | null>(null);
  const [ruleName, setRuleName] = useState('');
  const [ruleDescription, setRuleDescription] = useState('');
  const [targetFilters, setTargetFilters] = useState<TagRuleCondition[]>([]);
  const [complianceCriteria, setComplianceCriteria] = useState<ComplianceCriteria>({
    logic: 'all',
    conditions: [],
  });
  const [tags, setTags] = useState<TagRuleTags>({});
  const [alertSettings, setAlertSettings] = useState<AlertSettings>({
    severity: 'high',
    category: 'security',
    threshold: null,
    silence_duration: null,
    criteria: null,
  });
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  const [status, setStatus] = useInlineStatus();
  const createMutation = useCreateTagRule();
  const createAlertMutation = useCreateAlertRule();

  const isCompliance = ruleType === 'compliance';
  const isAlert = ruleType === 'alert';
  // A filter is "ready" if it has a value OR uses a valueless operator
  // (is_empty / is_not_empty), which intentionally don't take input.
  const isFilterReady = (f: UIFilterCondition) => isFilterValueReady(f.operator, f.value);
  const filledFilterCount = targetFilters.filter(isFilterReady).length;
  const filledCriteriaCount = complianceCriteria.conditions.filter(isFilterReady).length;

  // Build steps dynamically based on rule type
  const steps: StepDef[] = [
    {
      key: 'type',
      title: 'Type',
      summary: ruleType && (
        <Badge variant={isCompliance ? 'warning' : isAlert ? 'danger' : 'default'}>
          {isCompliance ? 'Compliance' : isAlert ? 'Alert' : 'Classification'}
        </Badge>
      ),
      content: <TypeSelector onSelect={handleSelectType} />,
    },
    {
      key: 'name',
      title: 'Name',
      summary: ruleName && (
        <div>
          <div>{ruleName}</div>
          {ruleDescription && <div>{ruleDescription.slice(0, 50)}{ruleDescription.length > 50 ? '...' : ''}</div>}
        </div>
      ),
      content: (
        <NameStep
          name={ruleName}
          onNameChange={setRuleName}
          description={ruleDescription}
          onDescriptionChange={setRuleDescription}
        />
      ),
    },
    {
      key: 'targets',
      title: (isCompliance || isAlert) ? 'Targets' : 'Filters',
      summary: filledFilterCount > 0 ? `${filledFilterCount} filter${filledFilterCount !== 1 ? 's' : ''}` : undefined,
      content: ruleType && (
        <TargetFilters
          ruleType={isAlert ? 'compliance' : ruleType}
          filters={targetFilters}
          onChange={setTargetFilters}
          complianceCriteria={complianceCriteria}
          onComplianceCriteriaChange={setComplianceCriteria}
          mode="targets-only"
        />
      ),
    },
    // Compliance: Criteria step
    ...(isCompliance ? [{
      key: 'criteria',
      title: 'Criteria',
      summary: filledCriteriaCount > 0 ? `${filledCriteriaCount} condition${filledCriteriaCount !== 1 ? 's' : ''}` : undefined,
      content: ruleType && (
        <TargetFilters
          ruleType={ruleType}
          filters={targetFilters}
          onChange={setTargetFilters}
          complianceCriteria={complianceCriteria}
          onComplianceCriteriaChange={setComplianceCriteria}
          mode="criteria-only"
        />
      ),
    }] : []),
    // Alert: Criteria step (same as compliance)
    ...(isAlert ? [{
      key: 'criteria',
      title: 'Criteria',
      summary: filledCriteriaCount > 0 ? `${filledCriteriaCount} condition${filledCriteriaCount !== 1 ? 's' : ''}` : undefined,
      content: (
        <TargetFilters
          ruleType="compliance"
          filters={targetFilters}
          onChange={setTargetFilters}
          complianceCriteria={complianceCriteria}
          onComplianceCriteriaChange={setComplianceCriteria}
          mode="criteria-only"
        />
      ),
    }] : []),
    // Alert: Settings step (severity, threshold, silence)
    ...(isAlert ? [{
      key: 'settings',
      title: 'Settings',
      summary: alertSettings.severity && (
        <Badge variant={alertSettings.severity === 'critical' ? 'danger' : alertSettings.severity === 'high' ? 'warning' : 'info'}>
          {alertSettings.severity}
        </Badge>
      ),
      content: (
        <AlertSettingsStep
          settings={alertSettings}
          onChange={setAlertSettings}
        />
      ),
    }] : []),
    // Classification/Compliance: Tags step
    ...(!isAlert ? [{
      key: 'tags',
      title: 'Tags',
      summary: isCompliance
        ? (tags.pass || tags.fail) && (
            <div>
              {tags.pass && <Badge variant="success">{tags.pass}</Badge>}{' '}
              {tags.fail && <Badge variant="danger">{tags.fail}</Badge>}
            </div>
          )
        : tags.label && <Badge color={tags.color || '#64748b'}>{tags.label}</Badge>,
      content: ruleType && (
        <TagsStep
          ruleType={ruleType as 'compliance' | 'classification'}
          tags={tags}
          onTagsChange={setTags}
        />
      ),
    }] : []),
  ];

  const totalSteps = steps.length;
  const isLastStep = activeIndex === totalSteps - 1;

  function handleSelectType(type: RuleType) {
    setRuleType(type);
    setActiveIndex(1);
  }

  function handleBack() {
    if (activeIndex === 1) {
      // Going back to type selection — reset type so centered view shows
      setActiveIndex(0);
      setRuleType(null);
    } else if (activeIndex > 0) {
      setActiveIndex((i) => i - 1);
    }
  }

  function handleContinue() {
    if (!isLastStep) setActiveIndex((i) => i + 1);
  }

  function handleCancelRequest() {
    setShowCancelConfirm(true);
  }

  function handleCancelConfirm() {
    setShowCancelConfirm(false);
    resetForm();
    onClose();
  }

  function resetForm() {
    setActiveIndex(0);
    setRuleType(null);
    setRuleName('');
    setRuleDescription('');
    setTargetFilters([]);
    setComplianceCriteria({ logic: 'all', conditions: [] });
    setTags({});
    setAlertSettings({ severity: 'high', category: 'security', threshold: null, silence_duration: null, criteria: null });
  }

  function handleSubmit() {
    if (!ruleType || !ruleName.trim()) return;

    if (isAlert) {
      const criteriaPayload = complianceCriteria.conditions.length > 0
        ? {
            logic: complianceCriteria.logic,
            conditions: complianceCriteria.conditions
              .filter(isFilterReady)
              .map((c) => ({ field: encodeFieldId(c.field_id), op: c.operator, value: c.value })),
          }
        : null;

      createAlertMutation.mutate({
        name: ruleName.trim(),
        description: ruleDescription.trim() || '',
        type: alertSettings.category,
        severity: alertSettings.severity as 'critical' | 'high' | 'medium' | 'low',
        target_filters: targetFilters
          .filter(isFilterReady)
          .map((f) => ({ field: encodeFieldId(f.field_id), op: f.operator, value: f.value })),
        compliance_criteria: criteriaPayload,
        threshold: alertSettings.threshold ? {
          condition: alertSettings.threshold.condition as 'falls_below' | 'exceeds',
          value: alertSettings.threshold.value,
          unit: alertSettings.threshold.unit,
        } : null,
        silence_duration: alertSettings.silence_duration,
        enabled: true,
      }, {
        onSuccess: () => {
          setStatus(null); // the wizard closes onto the rule in its group
          resetForm();
          onCreated();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: err.message || 'Failed to create alert rule' });
        },
      });
      return;
    }

    const payload = {
      name: ruleName.trim(),
      description: ruleDescription.trim() || undefined,
      type: ruleType as 'compliance' | 'classification',
      enabled: true,
      // Strip incomplete rows (operator-but-no-value, with a non-valueless
      // operator) so the backend never sees half-filled filters from
      // default-seeded "+ Add Filter" rows the user didn't fill.
      target_filters: targetFilters.filter(isFilterReady),
      ...(isCompliance && {
        compliance_criteria: {
          ...complianceCriteria,
          conditions: complianceCriteria.conditions.filter(isFilterReady),
        },
      }),
      tags,
    };

    createMutation.mutate(payload, {
      onSuccess: () => {
        setStatus(null); // the wizard closes onto the rule in its group
        resetForm();
        onCreated();
      },
      onError: (err) => {
        setStatus({ kind: 'error', message: err.message || 'Failed to create rule' });
      },
    });
  }

  function isContinueDisabled(): boolean {
    const step = steps[activeIndex];
    if (!step) return true;
    if (step.key === 'type') return ruleType === null;
    if (step.key === 'name') return !ruleName.trim();
    if (step.key === 'targets') return !targetFilters.some(isFilterReady);
    if (step.key === 'criteria') return !complianceCriteria.conditions.some(isFilterReady);
    if (step.key === 'tags') {
      if (isCompliance) return !tags.pass?.trim() || !tags.fail?.trim();
      return !tags.label?.trim();
    }
    // Settings is the alert flow's last step — re-validate prior required fields
    // so the Create Rule button can't fire if a back-edit emptied them.
    if (step.key === 'settings') {
      return !ruleName.trim() || !targetFilters.some(isFilterReady);
    }
    return false;
  }

  const continueDisabled = isContinueDisabled();

  const wizardClasses = [
    styles.wizard,
    isOpen ? styles.wizardOpen : styles.wizardClosed,
  ].filter(Boolean).join(' ');

  return (
    <>
      <div className={wizardClasses}>
        <InlineStatus status={status} />
        {!ruleType ? (
          /* Step 1 solo — centered type selection */
          <div className={styles.wizardTypeOnly}>
            <div className={styles.wizColHeader}>
              <div className={`${styles.wizColDot} ${styles.wizDotActive}`}>1</div>
              <span className={`${styles.wizColLabel} ${styles.wizLabelActive}`}>Choose Rule Type</span>
            </div>
            <div className={styles.wizColBody}>
              <TypeSelector onSelect={handleSelectType} />
            </div>
          </div>
        ) : (
          /* All steps — fluid columns */
          <div className={styles.wizardColumns}>
            {steps.map((step, i) => (
              <WizardStep
                key={step.key}
                stepIndex={i}
                activeIndex={activeIndex}
                title={step.title}
                summary={step.summary}
              >
                {step.content}
              </WizardStep>
            ))}
          </div>
        )}

        <div className={styles.wizardFooter}>
          <div className={styles.wizardFooterLeft}>
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
              onClick={handleSubmit}
              disabled={continueDisabled || createMutation.isPending}
              loading={createMutation.isPending}
            >
              Create Rule
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
