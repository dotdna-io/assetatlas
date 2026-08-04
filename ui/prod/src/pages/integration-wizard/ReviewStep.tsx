// SPDX-License-Identifier: AGPL-3.0-or-later
import { Link } from 'react-router-dom';
import { getConnectorDisplayName, getScheduleDescription, FRAMEWORK_FIELDS, isSensitiveField, getSourceMethodLabel } from '@/pages/integrations/integration-utils';
import type { WizardFormData, WizardMode, WizardStep } from './wizard-state';
import styles from './ReviewStep.module.css';

interface ReviewStepProps {
  formData: WizardFormData;
  mode: WizardMode;
  onGoToStep: (step: WizardStep) => void;
  onUpdate: (patch: Partial<WizardFormData>) => void;
}

export function ReviewStep({ formData, mode, onGoToStep, onUpdate }: ReviewStepProps) {
  const mappedTargets = new Set(formData.field_mapping.map(m => m.target));

  return (
    <div className={styles.review}>
      {/* Top row: Basic Details + Connection + Schedule */}
      <div className={styles.topRow}>
        {/* Basic Details */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <span className={styles.cardTitle}>Basic Details</span>
            <button type="button" className={styles.editButton} onClick={() => onGoToStep(1)}>Edit</button>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.row}><span className={styles.label}>Name</span><span className={styles.value}>{formData.name}</span></div>
            <div className={styles.row}><span className={styles.label}>Type</span><span className={styles.value}>{getConnectorDisplayName(formData.integration_type || '')}</span></div>
            {formData.source_method !== 'direct' && (
              <div className={styles.row}>
                <span className={styles.label}>Source</span>
                <span className={styles.value}>via {getSourceMethodLabel(formData.source_method)}</span>
              </div>
            )}
            <div className={styles.row}><span className={styles.label}>Category</span><span className={styles.value}>{formData.category || '—'}</span></div>
            <div className={styles.row}>
              <span className={styles.label}>Status</span>
              <span className={styles.value}>
                <span className={formData.enabled ? styles.statusOk : styles.statusMuted}>{formData.enabled ? 'Enabled' : 'Disabled'}</span>
              </span>
            </div>
            {formData.description && <div className={styles.row}><span className={styles.label}>Description</span><span className={styles.value}>{formData.description}</span></div>}
          </div>
        </div>

        {/* Connection */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <span className={styles.cardTitle}>Connection</span>
            <button type="button" className={styles.editButton} onClick={() => onGoToStep(2)}>Edit</button>
          </div>
          <div className={styles.cardBody}>
            {Object.entries(formData.config).filter(([k]) => !k.startsWith('_')).map(([key, value]) => (
              <div key={key} className={styles.row}>
                <span className={styles.label}>{key.replace(/_/g, ' ')}</span>
                <span className={styles.value}>
                  {typeof value === 'string' && isSensitiveField(key) ? '••••••••' : String(value)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Schedule */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <span className={styles.cardTitle}>Schedule</span>
            <button type="button" className={styles.editButton} onClick={() => onGoToStep(2)}>Edit</button>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.row}>
              <span className={styles.label}>Enabled</span>
              <span className={styles.value}>{formData.schedule.enabled ? 'Yes' : 'No'}</span>
            </div>
            {formData.schedule.enabled && (
              <>
                <div className={styles.row}><span className={styles.label}>Frequency</span><span className={styles.value}>{getScheduleDescription(formData.schedule.cron)}</span></div>
                <div className={styles.row}><span className={styles.label}>Timezone</span><span className={styles.value}>{formData.schedule.timezone}</span></div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Field Mapping — full width */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <span className={styles.cardTitle}>Field Mapping</span>
          <button type="button" className={styles.editButton} onClick={() => onGoToStep(3)}>Edit</button>
        </div>
        <div className={styles.mappingBody}>
          {formData.field_mapping.length > 0 ? (
            <div className={styles.mappingGrid}>
              <div className={styles.mappingHeaderRow}>
                <span className={styles.mappingColHeader}>Source Field</span>
                <span className={styles.mappingColHeader} />
                <span className={styles.mappingColHeader}>Target Field</span>
              </div>
              {formData.field_mapping.map((m, i) => {
                const targetField = FRAMEWORK_FIELDS.find(f => f.key === m.target);
                const isIdentifier = ['hostname', 'fqdn', 'ip', 'mac'].includes(m.target);
                return (
                  <div key={i} className={styles.mappingRow}>
                    <span className={styles.mappingSource}>{m.source}</span>
                    <span className={styles.mappingArrow}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                    </span>
                    <span className={styles.mappingTarget}>
                      {targetField?.label || m.target}
                      {isIdentifier && <span className={styles.identifierBadge}>ID</span>}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className={styles.emptyRow}>No field mappings configured</div>
          )}

          {/* Preserve fields */}
          {formData.preserve_fields.length > 0 && (
            <div className={styles.mappingSection}>
              <div className={styles.mappingSectionTitle}>Data Source Specific Fields</div>
              <div className={styles.chipList}>
                {formData.preserve_fields.map(f => (
                  <span key={f} className={styles.chip}>{f}</span>
                ))}
              </div>
            </div>
          )}

          {/* Transformations */}
          {formData.transformations.length > 0 && (
            <div className={styles.mappingSection}>
              <div className={styles.mappingSectionTitle}>Transformations</div>
              {formData.transformations.map(t => (
                <div key={t.name} className={styles.transformRow}>
                  <span className={styles.transformName}>{t.name}</span>
                  <span className={styles.transformOps}>
                    {t.operations.map(op => op.type).join(' → ')}
                  </span>
                  {mappedTargets.has(t.name) || formData.preserve_fields.includes(t.name) ? (
                    <span className={styles.statusOk}>Mapped</span>
                  ) : (
                    <span className={styles.statusMuted}>Unused</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Data filters */}
          {formData.data_filters.length > 0 && (
            <div className={styles.mappingSection}>
              <div className={styles.mappingSectionTitle}>Data Filters</div>
              <div className={styles.row}>
                <span className={styles.label}>{formData.data_filters.length} active filter{formData.data_filters.length !== 1 ? 's' : ''}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Run-pipeline opt-in — shown when creating/cloning a source. UI only
          for now: the choice is captured in formData and wired to a pipeline
          run once the backend supports it (see IntegrationWizardPage create
          onSuccess). */}
      {mode !== 'edit' && (
        <div className={styles.pipelineOptIn}>
          <label className={styles.pipelineRow}>
            <input
              type="checkbox"
              checked={formData.run_pipeline_after_create}
              onChange={(e) => onUpdate({ run_pipeline_after_create: e.target.checked })}
            />
            <span className={styles.pipelineLabel}>Run the asset pipeline once this data source is created</span>
          </label>
          <p className={styles.pipelineHint}>
            Records collected from a new source become inventory assets only after the pipeline runs. You can also run it or set a schedule anytime from{' '}
            <Link to="/settings/data/pipeline" target="_blank" rel="noopener noreferrer" className={styles.pipelineLink}>Settings → Data → Pipeline</Link>.
          </p>
        </div>
      )}
    </div>
  );
}
