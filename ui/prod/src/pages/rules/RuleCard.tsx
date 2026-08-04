// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect, useCallback } from 'react';
import type { TagRule, TagRuleCondition, ComplianceCriteria, DetectionConfig, TagRuleTags } from '@/api/tag-rules';
import { useToggleTagRule, useDeleteTagRule, useUpdateTagRule, useTagRuleFields, tagRulesApi } from '@/api/tag-rules';
import { FilterRow, emptyCondition, isFilterValueReady } from '@/components/ui/FilterRow';
import type { FilterCondition } from '@/components/ui/FilterRow';
import { ColorPicker } from '@/components/ui/ColorPicker';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { DeleteIconButton } from '@/components/ui/IconButton';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './rules.module.css';

interface RuleCardProps {
  rule: TagRule;
}

// FilterRow emits canonical operator codes, so edit-form conditions are sent to
// preview/save as-is. The write model validates via the FilterOperator enum and
// persists its spaced .value label; every read path (the operator registry)
// resolves whichever spelling is stored, so both migration 109's one-time
// normalization and ad-hoc wizard saves evaluate correctly. (Collapsed cards
// render no per-condition operator string — the edit-form FilterRows show
// resolver labels — so no raw code leaks to the user.)
export function RuleCard({ rule }: RuleCardProps) {
  const [status, setStatus] = useInlineStatus();
  const cardRef = useRef<HTMLDivElement>(null);

  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  // Full description opens in a click-to-expand detail row (Rules density rework).
  const [descExpanded, setDescExpanded] = useState(false);
  // Edit form state
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editTargetFilters, setEditTargetFilters] = useState<TagRuleCondition[]>([]);
  const [editComplianceCriteria, setEditComplianceCriteria] = useState<ComplianceCriteria>({ logic: 'all', conditions: [] });
  const [editTags, setEditTags] = useState<TagRuleTags>({});

  // Live preview during edit
  const [previewCount, setPreviewCount] = useState<number | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const fetchPreview = useCallback(() => {
    if (!isEditing) return;
    const hasFilters = editTargetFilters.some(
      (f) => isFilterValueReady(f.operator, f.value)
    );
    if (!hasFilters) {
      setPreviewCount(null);
      return;
    }
    setPreviewLoading(true);
    tagRulesApi.preview({
      target_filters: editTargetFilters,
      ...(rule.type === 'compliance' && editComplianceCriteria.conditions.length > 0
        ? { compliance_criteria: { logic: editComplianceCriteria.logic, conditions: editComplianceCriteria.conditions } }
        : {}),
    }).then((res) => {
      setPreviewCount(res.total_matching ?? 0);
    }).catch(() => {
      setPreviewCount(null);
    }).finally(() => {
      setPreviewLoading(false);
    });
  }, [isEditing, editTargetFilters, editComplianceCriteria, rule.type]);

  useEffect(() => {
    if (!isEditing) return;
    clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(fetchPreview, 500);
    return () => clearTimeout(previewTimerRef.current);
  }, [isEditing, fetchPreview]);

  const toggleMutation = useToggleTagRule();
  const deleteMutation = useDeleteTagRule();
  const updateMutation = useUpdateTagRule();

  const { data: fieldsData } = useTagRuleFields();
  const targetFields = fieldsData?.entries ?? [];
  const complianceFields = fieldsData?.entries ?? [];

  function handleToggle() {
    toggleMutation.mutate(rule.id, {
      onSuccess: () => {
        setStatus(null); // the toggle itself shows the new state
      },
      onError: () => {
        setStatus({ kind: 'error', message: 'Failed to toggle rule' });
      },
    });
  }

  function handleDelete() {
    deleteMutation.mutate(rule.id, {
      onSuccess: () => {
        setStatus(null); // the card leaves the list
      },
      onError: () => {
        setStatus({ kind: 'error', message: 'Failed to delete rule' });
      },
    });
    setIsConfirmOpen(false);
  }

  function handleEditOpen() {
    setEditName(rule.name);
    setEditDescription(rule.description ?? '');
    setEditTargetFilters(rule.target_filters.length > 0 ? rule.target_filters : []);
    setEditComplianceCriteria(
      rule.compliance_criteria ?? { logic: 'all', conditions: [] }
    );
    setEditTags({ ...rule.tags });
    setIsEditing(true);
    setTimeout(() => {
      cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 0);
  }

  function handleCancel() {
    setIsEditing(false);
  }

  function handleSave() {
    const data = {
      name: editName.trim() || undefined,
      description: editDescription,
      target_filters: editTargetFilters,
      ...(rule.type === 'compliance' && {
        compliance_criteria: {
          logic: editComplianceCriteria.logic,
          conditions: editComplianceCriteria.conditions,
        },
      }),
      tags: editTags,
    };

    updateMutation.mutate({ id: rule.id, data }, {
      onSuccess: () => {
        setStatus(null); // the card shows the change
        setIsEditing(false);
      },
      onError: (err) => {
        setStatus({ kind: 'error', message: err.message || 'Failed to update rule' });
      },
    });
  }

  const typeColor = rule.type === 'compliance' ? '#f97316' : '#3b82f6';
  const typeLabel = rule.type === 'compliance' ? 'Compliance' : 'Classification';

  const isToggling = toggleMutation.isPending;
  const isDeleting = deleteMutation.isPending;
  const isSaving = updateMutation.isPending;

  return (
    <>
      <div
        ref={cardRef}
        className={`${styles.ruleCard} ${isEditing ? styles.ruleCardExpanded : ''}`}
      >
        <InlineStatus status={status} />
        <div
          className={styles.ruleCardView}
          onClick={rule.description ? () => setDescExpanded((v) => !v) : undefined}
          role={rule.description ? 'button' : undefined}
          tabIndex={rule.description ? 0 : undefined}
          aria-expanded={rule.description ? descExpanded : undefined}
          onKeyDown={rule.description ? (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setDescExpanded((v) => !v); }
          } : undefined}
          style={rule.description ? { cursor: 'pointer' } : undefined}
        >
          {rule.description && (
            <span
              className={`${styles.discloseCaret} ${descExpanded ? styles.discloseCaretOpen : ''}`}
              aria-hidden="true"
            >
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 18l6-6-6-6" />
              </svg>
            </span>
          )}

          <div className={styles.ruleInfo}>
            <div className={styles.ruleNameRow}>
              <span className={styles.ruleName}>{rule.name}</span>
            </div>
          </div>

          <div className={styles.ruleTags}>
            {rule.type === 'compliance' ? (
              <>
                {rule.tags.pass && (
                  <Badge variant="success">{rule.tags.pass}</Badge>
                )}
                {rule.tags.fail && (
                  <Badge variant="danger">{rule.tags.fail}</Badge>
                )}
              </>
            ) : (
              <>
                {rule.tags.label && (
                  <Badge color={rule.tags.color}>{rule.tags.label}</Badge>
                )}
              </>
            )}
          </div>

          <div className={styles.actionSep} />

          <div className={styles.ruleMetrics}>
            <span>
              <span className={styles.metricValue}>
                {rule.last_execution_assets_matched.toLocaleString()}
              </span>{' '}
              assets
            </span>
            {rule.type === 'compliance' && (
              <>
                {rule.last_execution_assets_passed != null && (
                  <span className={styles.metricPass}>
                    <span className={styles.metricValue}>
                      {rule.last_execution_assets_passed.toLocaleString()}
                    </span>{' '}
                    pass
                  </span>
                )}
                {rule.last_execution_assets_failed != null && (
                  <span className={styles.metricFail}>
                    <span className={styles.metricValue}>
                      {rule.last_execution_assets_failed.toLocaleString()}
                    </span>{' '}
                    fail
                  </span>
                )}
              </>
            )}
          </div>

          <div className={styles.actionSep} />

          <div className={styles.ruleActions} onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className={styles.editBtn}
              onClick={handleEditOpen}
              aria-label="Edit rule"
              title="Edit rule"
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
            <DeleteIconButton
              className={styles.editBtn}
              onClick={() => setIsConfirmOpen(true)}
              disabled={isDeleting}
              aria-label="Delete rule"
              title="Delete rule"
              size={16}
            />
            <span className={styles.metaText} style={{ color: typeColor }}>{typeLabel}</span>
            <span className={styles.metaSep}>|</span>
            <ToggleSwitch
              checked={rule.enabled}
              onChange={handleToggle}
              disabled={isToggling}
            />
          </div>
        </div>
        {descExpanded && rule.description && (
          <div className={styles.ruleDesc}>{rule.description}</div>
        )}

        {isEditing && rule.type === 'classification' && (
          <div className={styles.editForm}>
            <div className={styles.editSection}>
              {/* Left column: identity */}
              <div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Name</label>
                  <input
                    className={styles.formInput}
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="Rule name"
                  />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Description</label>
                  <textarea
                    className={styles.formTextarea}
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    placeholder="Rule description"
                  />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Tag Label</label>
                  <input
                    className={styles.formInput}
                    type="text"
                    value={editTags.label ?? ''}
                    onChange={(e) => setEditTags((t) => ({ ...t, label: e.target.value }))}
                    placeholder="Tag label"
                  />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Color</label>
                  <ColorPicker
                    value={editTags.color ?? '#64748b'}
                    onChange={(color) => setEditTags((t) => ({ ...t, color }))}
                  />
                </div>
              </div>

              {/* Right column: targeting */}
              <div>
                <div className={styles.filterSection}>
                  <div className={styles.filterSectionHeader}>Target Assets</div>
                  {editTargetFilters.map((condition, i) => (
                    <FilterRow
                      key={i}
                      fields={targetFields}
                      condition={condition}
                      onChange={(c: FilterCondition) => {
                        setEditTargetFilters((prev) => prev.map((f, idx) => idx === i ? c : f));
                      }}
                      onRemove={() => {
                        setEditTargetFilters((prev) => prev.filter((_, idx) => idx !== i));
                      }}
                    />
                  ))}
                  <button
                    type="button"
                    className={styles.addFilterBtn}
                    onClick={() => setEditTargetFilters((prev) => [...prev, emptyCondition(targetFields)])}
                  >
                    + Add Filter
                  </button>
                </div>
                <div className={styles.ruleMetrics} style={{ marginTop: 16 }}>
                  <span>Assets matched: </span>
                  <span className={`${styles.metricAccent} ${styles.metricValue}`}>
                    {previewLoading ? '...' : (previewCount ?? rule.last_execution_assets_matched ?? 0).toLocaleString()}
                  </span>
                </div>
              </div>
            </div>

            <div className={styles.editActions}>
              <Button variant="secondary" size="sm" onClick={handleCancel} disabled={isSaving}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" onClick={handleSave} loading={isSaving}>
                Save Changes
              </Button>
            </div>
          </div>
        )}

        {isEditing && rule.type === 'compliance' && (
          <div className={styles.editForm}>
            <div className={styles.editSection}>
              {/* Left column: identity + tags + detection */}
              <div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Name</label>
                  <input
                    className={styles.formInput}
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="Rule name"
                  />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Description</label>
                  <textarea
                    className={styles.formTextarea}
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    placeholder="Rule description"
                  />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Pass tag</label>
                  <input
                    className={styles.formInput}
                    type="text"
                    value={editTags.pass ?? ''}
                    onChange={(e) => setEditTags((t) => ({ ...t, pass: e.target.value }))}
                    placeholder="e.g. compliant"
                  />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Fail tag</label>
                  <input
                    className={styles.formInput}
                    type="text"
                    value={editTags.fail ?? ''}
                    onChange={(e) => setEditTags((t) => ({ ...t, fail: e.target.value }))}
                    placeholder="e.g. non-compliant"
                  />
                </div>

              </div>

              {/* Right column: targeting + criteria + preview */}
              <div>
                <div className={styles.filterSection}>
                  <div className={styles.filterSectionHeader}>Target Assets</div>
                  {editTargetFilters.map((condition, i) => (
                    <FilterRow
                      key={i}
                      fields={targetFields}
                      condition={condition}
                      onChange={(c: FilterCondition) => {
                        setEditTargetFilters((prev) => prev.map((f, idx) => idx === i ? c : f));
                      }}
                      onRemove={() => {
                        setEditTargetFilters((prev) => prev.filter((_, idx) => idx !== i));
                      }}
                    />
                  ))}
                  <button
                    type="button"
                    className={styles.addFilterBtn}
                    onClick={() => setEditTargetFilters((prev) => [...prev, emptyCondition(targetFields)])}
                  >
                    + Add Filter
                  </button>
                </div>

                <div className={styles.filterSection}>
                  <div className={styles.filterSectionHeader}>Compliance Criteria</div>
                  <div className={styles.logicToggle}>
                    <button
                      type="button"
                      className={[
                        styles.logicBtn,
                        styles.logicBtnLeft,
                        editComplianceCriteria.logic === 'all' ? styles.logicBtnActive : '',
                      ].filter(Boolean).join(' ')}
                      onClick={() => setEditComplianceCriteria((c) => ({ ...c, logic: 'all' }))}
                    >
                      ALL conditions
                    </button>
                    <button
                      type="button"
                      className={[
                        styles.logicBtn,
                        styles.logicBtnRight,
                        editComplianceCriteria.logic === 'any' ? styles.logicBtnActive : '',
                      ].filter(Boolean).join(' ')}
                      onClick={() => setEditComplianceCriteria((c) => ({ ...c, logic: 'any' }))}
                    >
                      ANY condition
                    </button>
                  </div>
                  {editComplianceCriteria.conditions.map((condition, i) => (
                    <FilterRow
                      key={i}
                      fields={complianceFields}
                      condition={condition}
                      onChange={(c: FilterCondition) => {
                        setEditComplianceCriteria((prev) => ({
                          ...prev,
                          conditions: prev.conditions.map((f, idx) => idx === i ? c : f),
                        }));
                      }}
                      onRemove={() => {
                        setEditComplianceCriteria((prev) => ({
                          ...prev,
                          conditions: prev.conditions.filter((_, idx) => idx !== i),
                        }));
                      }}
                    />
                  ))}
                  <button
                    type="button"
                    className={styles.addFilterBtn}
                    onClick={() => {
                      setEditComplianceCriteria((prev) => ({
                        ...prev,
                        conditions: [...prev.conditions, emptyCondition(complianceFields)],
                      }));
                    }}
                  >
                    + Add Condition
                  </button>
                </div>

                <div className={styles.ruleMetrics} style={{ marginTop: 16, gap: 16 }}>
                  <span>Assets matched: <span className={`${styles.metricAccent} ${styles.metricValue}`}>{previewLoading ? '...' : (previewCount ?? rule.last_execution_assets_matched ?? 0).toLocaleString()}</span></span>
                  <span>Pass: <span className={`${styles.metricPass} ${styles.metricValue}`}>{previewLoading ? '...' : (rule.last_execution_assets_passed ?? 0).toLocaleString()}</span></span>
                  <span>Fail: <span className={`${styles.metricFail} ${styles.metricValue}`}>{previewLoading ? '...' : (rule.last_execution_assets_failed ?? 0).toLocaleString()}</span></span>
                </div>
              </div>
            </div>

            <div className={styles.editActions}>
              <Button variant="secondary" size="sm" onClick={handleCancel} disabled={isSaving}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" onClick={handleSave} loading={isSaving}>
                Save Changes
              </Button>
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={isConfirmOpen}
        title="Delete Rule"
        message={`Are you sure you want to delete "${rule.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setIsConfirmOpen(false)}
        variant="danger"
      />
    </>
  );
}
