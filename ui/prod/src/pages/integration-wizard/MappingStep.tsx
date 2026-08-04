// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { FieldMappingStep } from './FieldMappingStep';
import { DataFiltersStep } from './DataFiltersStep';
import { TransformationsStep } from './TransformationsStep';
import type { WizardFormData } from './wizard-state';
import styles from './ConnectionStep.module.css'; // reuse same section styles

interface MappingStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
}

export function MappingStep({ formData, onUpdate }: MappingStepProps) {
  const [filtersOpen, setFiltersOpen] = useState(formData.data_filters.length > 0);
  const [transformsOpen, setTransformsOpen] = useState(formData.transformations.length > 0);

  const filterCount = formData.data_filters.length;
  const transformCount = formData.transformations.length;

  return (
    <div className={styles.sections}>
      {/* Data Filters — collapsible, collapsed by default if no filters */}
      <div className={styles.section}>
        <button type="button" className={styles.sectionHeader} onClick={() => setFiltersOpen(!filtersOpen)}>
          <span className={styles.sectionTitle}>
            Data Filters {filterCount > 0 ? `(${filterCount} active)` : '(optional)'}
          </span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`${styles.chevron} ${filtersOpen ? styles.chevronOpen : ''}`}><path d="M6 9l6 6 6-6"/></svg>
        </button>
        {filtersOpen && (
          <div className={styles.sectionBody}>
            <DataFiltersStep formData={formData} onUpdate={onUpdate} />
          </div>
        )}
      </div>

      {/* Transformations — collapsible, collapsed by default if no transforms */}
      <div className={styles.section}>
        <button type="button" className={styles.sectionHeader} onClick={() => setTransformsOpen(!transformsOpen)}>
          <span className={styles.sectionTitle}>
            Transformations {transformCount > 0 ? `(${transformCount} active)` : '(optional)'}
          </span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`${styles.chevron} ${transformsOpen ? styles.chevronOpen : ''}`}><path d="M6 9l6 6 6-6"/></svg>
        </button>
        {transformsOpen && (
          <div className={styles.sectionBody}>
            <TransformationsStep formData={formData} onUpdate={onUpdate} />
          </div>
        )}
      </div>

      {/* Field Mapping — always visible */}
      <div className={styles.section}>
        <div className={styles.sectionBody}>
          <FieldMappingStep formData={formData} onUpdate={onUpdate} />
        </div>
      </div>
    </div>
  );
}
