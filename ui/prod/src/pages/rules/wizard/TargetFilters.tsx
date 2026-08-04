// SPDX-License-Identifier: AGPL-3.0-or-later
import { FilterRow, emptyCondition } from '@/components/ui/FilterRow';
import type { FilterCondition } from '@/components/ui/FilterRow';
import { useTagRuleFields } from '@/api/tag-rules';
import type { TagRuleCondition, ComplianceCriteria } from '@/api/tag-rules';
import { Button } from '@/components/ui/Button';
import { PreviewCounter } from './PreviewCounter';
import styles from '../rules.module.css';

interface TargetFiltersProps {
  ruleType: 'compliance' | 'classification';
  filters: TagRuleCondition[];
  onChange: (filters: TagRuleCondition[]) => void;
  complianceCriteria: ComplianceCriteria;
  onComplianceCriteriaChange: (criteria: ComplianceCriteria) => void;
  mode?: 'all' | 'targets-only' | 'criteria-only';
}

export function TargetFilters({
  ruleType,
  filters,
  onChange,
  complianceCriteria,
  onComplianceCriteriaChange,
  mode = 'all',
}: TargetFiltersProps) {
  const { data: fieldsData, isLoading } = useTagRuleFields();

  const targetFields = fieldsData?.entries ?? [];
  const complianceFields = fieldsData?.entries ?? [];

  // --- Target filter handlers ---
  function handleAddTarget() {
    onChange([...filters, emptyCondition(targetFields)]);
  }

  function handleChangeTarget(index: number, condition: FilterCondition) {
    onChange(filters.map((f, i) => (i === index ? condition : f)));
  }

  function handleRemoveTarget(index: number) {
    onChange(filters.filter((_, i) => i !== index));
  }

  // --- Compliance criteria handlers ---
  function handleLogicChange(logic: 'all' | 'any') {
    onComplianceCriteriaChange({ ...complianceCriteria, logic });
  }

  function handleAddCompliance() {
    const newCondition = emptyCondition(complianceFields);
    onComplianceCriteriaChange({
      ...complianceCriteria,
      conditions: [...complianceCriteria.conditions, newCondition],
    });
  }

  function handleChangeCompliance(index: number, condition: FilterCondition) {
    const updated = complianceCriteria.conditions.map((c, i) => (i === index ? condition : c));
    onComplianceCriteriaChange({ ...complianceCriteria, conditions: updated });
  }

  function handleRemoveCompliance(index: number) {
    onComplianceCriteriaChange({
      ...complianceCriteria,
      conditions: complianceCriteria.conditions.filter((_, i) => i !== index),
    });
  }

  const targetPanel = (
    <div>
      <div className={styles.filterSectionHeader}>Target Assets</div>

      {isLoading && (
        <div className={styles.filterLoadingText}>Loading fields...</div>
      )}

      {filters.map((condition, i) => (
        <FilterRow
          key={i}
          fields={targetFields}
          condition={condition}
          onChange={(c) => handleChangeTarget(i, c)}
          onRemove={() => handleRemoveTarget(i)}
        />
      ))}

      <Button variant="secondary" size="sm" onClick={handleAddTarget} className={styles.addFilterBtnSpaced}>
        + Add Filter
      </Button>
    </div>
  );

  const criteriaPanel = (
    <div>
      <div className={styles.filterSectionHeader}>Compliance Criteria</div>

      <div className={styles.logicToggle}>
        <button
          type="button"
          className={[
            styles.logicBtn,
            styles.logicBtnLeft,
            complianceCriteria.logic === 'all' ? styles.logicBtnActive : '',
          ]
            .filter(Boolean)
            .join(' ')}
          onClick={() => handleLogicChange('all')}
        >
          ALL conditions
        </button>
        <button
          type="button"
          className={[
            styles.logicBtn,
            styles.logicBtnRight,
            complianceCriteria.logic === 'any' ? styles.logicBtnActive : '',
          ]
            .filter(Boolean)
            .join(' ')}
          onClick={() => handleLogicChange('any')}
        >
          ANY condition
        </button>
      </div>

      {isLoading && (
        <div className={styles.filterLoadingText}>Loading fields...</div>
      )}

      {complianceCriteria.conditions.map((condition, i) => (
        <FilterRow
          key={i}
          fields={complianceFields}
          condition={condition}
          onChange={(c) => handleChangeCompliance(i, c)}
          onRemove={() => handleRemoveCompliance(i)}
        />
      ))}

      <Button variant="secondary" size="sm" onClick={handleAddCompliance} className={styles.addFilterBtnSpaced}>
        + Add Condition
      </Button>

      <PreviewCounter filters={filters} complianceCriteria={complianceCriteria} />
    </div>
  );

  // Targets only (used as separate step for compliance, or single step for classification)
  if (mode === 'targets-only' || ruleType === 'classification') {
    return (
      <div className={styles.filtersSingle}>
        {targetPanel}
        <PreviewCounter filters={filters} />
      </div>
    );
  }

  // Criteria only (compliance step 4)
  if (mode === 'criteria-only') {
    return <div className={styles.filtersSingle}>{criteriaPanel}</div>;
  }

  // All mode (legacy — two-column layout for edit mode)
  return (
    <div>
      <div className={styles.filtersGrid}>
        {targetPanel}
        {criteriaPanel}
      </div>
    </div>
  );
}
