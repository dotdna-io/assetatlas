// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import type { AlertRule, AlertRuleCondition, AlertRuleThreshold, AnomalyThreshold } from '@/api/alerts';
import { useToggleAlertRule, useDeleteAlertRule, useUpdateAlertRule, useUpdateAnomalyThreshold } from '@/api/alerts';
import { TargetFilters } from '@/pages/rules/wizard/TargetFilters';
import { ThresholdEditor } from '@/pages/rules/wizard/ThresholdEditor';
import { isFilterValueReady } from '@/components/ui/FilterRow';
import { DeleteIconButton } from '@/components/ui/IconButton';
import { encodeFieldId, coerceFieldId } from '@/types/field-id';
import type { TagRuleCondition, ComplianceCriteria } from '@/api/tag-rules';
import { Button } from '@/components/ui/Button';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { SEVERITY_COLORS } from '@/constants/severity';
import { useAuth } from '@/auth/useAuth';
import styles from './alerts.module.css';

import {
  ALERT_TYPE_COLORS as TYPE_COLORS,
  ALERT_TYPE_LABELS as TYPE_LABELS,
} from '@/constants/alertTypes';

// Reversed mirror of RuleWizard's create-time mapping (RuleWizard.tsx:291-302):
// stored rules carry an encoded `field` string; the wizard editors work in
// TagRuleCondition (typed FieldId + `operator`). coerceFieldId is non-throwing —
// a legacy/undecodable `field` degrades to an unselected row (re-pick to fix)
// rather than crashing the Edit handler.
function toUiCondition(c: AlertRuleCondition): TagRuleCondition {
  return { field_id: coerceFieldId(c.field) ?? { kind: 'static', name: '' }, operator: c.op, value: c.value };
}

function toApiCondition(c: TagRuleCondition): AlertRuleCondition {
  return { field: encodeFieldId(c.field_id), op: c.operator, value: c.value };
}

const EMPTY_CRITERIA: ComplianceCriteria = { logic: 'all', conditions: [] };

// A condition is saveable only when a real field is selected AND its value is
// complete for the operator. The field check stops an undecodable stored field
// (degraded by toUiCondition to the empty `static:` sentinel) from being silently
// re-encoded and persisted as `field: "static:"`; the value check mirrors the
// wizard's create-time readiness gate (RuleWizard filters both targets and criteria).
function isConditionReady(c: TagRuleCondition): boolean {
  const fieldSelected = !(c.field_id.kind === 'static' && c.field_id.name === '');
  return fieldSelected && isFilterValueReady(c.operator, c.value);
}

// ── ML sensitivity slider (#951; merged to one two-handle track) ────────────
// Each ML alert card exposes its medium/high outlier cutoffs as one plain-language
// track with two handles — High (stricter, left) and Medium (looser, right) — sharing
// a single scale (no numbers shown). A handle position ∈ [0,1] maps log-scale to a
// cutoff percentage over [PCT_MIN, PCT_MAX]: geometric, so the low end — where the
// defaults (medium 0.5%, high 0.1%) sit — keeps its resolution. The backend still
// stores and receives the raw percentages via the anomaly-threshold route (unchanged).
const PCT_MIN = 0.05; // strictest — "Only the most unusual"
const PCT_MAX = 5; // loosest — "Flag anything slightly odd"
const DEFAULT_PCT_MEDIUM = 0.5;
const DEFAULT_PCT_HIGH = 0.1;

// Stored percentage → slider position ∈ [0,1] (stored value first clamped into range).
function pctToPos(pct: number): number {
  const clamped = Math.min(PCT_MAX, Math.max(PCT_MIN, pct));
  return Math.log(clamped / PCT_MIN) / Math.log(PCT_MAX / PCT_MIN);
}

// Slider position ∈ [0,1] → percentage, rounded to 2 significant figures so the saved
// payload stays clean (e.g. 0.5, 0.1) instead of carrying float noise.
function posToPct(pos: number): number {
  const pct = PCT_MIN * Math.pow(PCT_MAX / PCT_MIN, pos);
  return Number(pct.toPrecision(2));
}

// Plain-language description of what a slider position flags — words only, no numbers
// (mirrors #951's "no percentages shown" rule). Buckets the log-scale position into
// five bands from strictest (left) to loosest (right). Both handles share this scale.
function sensitivityPhrase(pos: number): string {
  if (pos < 0.16) return 'only the most extreme outliers';
  if (pos < 0.36) return 'clearly unusual assets';
  if (pos < 0.62) return 'moderately unusual assets';
  if (pos < 0.83) return 'anything a bit out of the ordinary';
  return 'almost anything slightly odd';
}

interface AlertCardProps {
  rule: AlertRule;
}

export function AlertCard({ rule }: AlertCardProps) {
  const { isAdmin } = useAuth();
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  // The full description lives in a click-to-expand detail row, keeping the
  // list scannable (see Rules-page density rework).
  const [descExpanded, setDescExpanded] = useState(false);
  const [editName, setEditName] = useState(rule.name);
  const [editDescription, setEditDescription] = useState(rule.description);
  const [editSeverity, setEditSeverity] = useState(rule.severity);
  const [editType, setEditType] = useState(rule.type);
  const [editSilence, setEditSilence] = useState<number | null>(rule.silence_duration);
  const [editFilters, setEditFilters] = useState<TagRuleCondition[]>([]);
  const [editCriteria, setEditCriteria] = useState<ComplianceCriteria>(EMPTY_CRITERIA);
  // threshold is a union: sql/filter rules carry {condition,value,unit}; anomaly
  // rules carry the detector's tuning params. Narrow with `in` before reading it.
  const sqlThreshold = rule.threshold && 'condition' in rule.threshold ? rule.threshold : null;
  // ML rules are identified by detector_key (their threshold no longer carries a
  // distinguishing key). Admins get an inline sensitivity editor for the medium/high
  // outlier gates, saved through the generic anomaly-threshold route.
  const isMlRule = rule.source === 'system' &&
    (rule.detector_key === 'ml_newcomer' || rule.detector_key === 'ml_behavior');
  const mlThreshold = isMlRule ? (rule.threshold as AnomalyThreshold | null) : null;
  // Slider positions ∈ [0,1] (log scale — see pctToPos/posToPct). Seed High clamped to
  // Medium so an already-invalid stored rule (high looser than medium) loads valid;
  // there is no reachable error state.
  const initMediumPos = pctToPos(mlThreshold?.pct_medium ?? DEFAULT_PCT_MEDIUM);
  const initHighPos = Math.min(pctToPos(mlThreshold?.pct_high ?? DEFAULT_PCT_HIGH), initMediumPos);
  const [mediumPos, setMediumPos] = useState(initMediumPos);
  const [highPos, setHighPos] = useState(initHighPos);
  const anomalyMutation = useUpdateAnomalyThreshold();
  const [editThreshold, setEditThreshold] = useState<AlertRuleThreshold | null>(sqlThreshold);
  const toggleMutation = useToggleAlertRule();
  const deleteMutation = useDeleteAlertRule();
  const updateMutation = useUpdateAlertRule();

  const borderColor = SEVERITY_COLORS[rule.severity] || '#64748b';
  const isUser = rule.source === 'user';
  const canEdit = isUser && isAdmin;
  // A rule must keep at least one valid target filter. Gate Save so clearing or
  // removing every row can't POST target_filters:[] (the backend rejects it, and
  // handleSave surfaces no error) or strand an unresolved field.
  const canSaveEdit = editFilters.some(isConditionReady);

  function handleToggle(enabled: boolean) {
    toggleMutation.mutate({ id: rule.id, enabled });
  }

  function handleDelete() {
    deleteMutation.mutate(rule.id);
    setIsConfirmOpen(false);
  }

  function handleEditOpen() {
    setEditName(rule.name);
    setEditDescription(rule.description);
    setEditSeverity(rule.severity);
    setEditType(rule.type);
    setEditSilence(rule.silence_duration);
    setEditFilters((rule.target_filters ?? []).map(toUiCondition));
    setEditCriteria(
      rule.compliance_criteria
        ? {
            logic: rule.compliance_criteria.logic,
            conditions: rule.compliance_criteria.conditions.map(toUiCondition),
          }
        : EMPTY_CRITERIA,
    );
    setEditThreshold(sqlThreshold);
    setIsEditing(true);
  }

  function handleEditCancel() {
    setIsEditing(false);
  }

  function handleSave() {
    const readyFilters = editFilters.filter(isConditionReady);
    const readyCriteria = editCriteria.conditions.filter(isConditionReady);
    updateMutation.mutate({
      id: rule.id,
      data: {
        name: editName.trim(),
        description: editDescription,
        type: editType,
        severity: editSeverity,
        silence_duration: editSilence,
        target_filters: readyFilters.map(toApiCondition),
        compliance_criteria:
          editType === 'compliance' && readyCriteria.length > 0
            ? { logic: editCriteria.logic, conditions: readyCriteria.map(toApiCondition) }
            : null,
        threshold: editThreshold,
      },
    }, {
      onSuccess: () => setIsEditing(false),
    });
  }

  // High is the rarer/urgent tier: it can never be looser (a larger cutoff = more
  // sensitive) than Medium. Dragging Medium down drags High with it; High can't pass
  // Medium. The rule therefore stays valid and Save is always enabled.
  function handleMediumPos(pos: number) {
    setMediumPos(pos);
    if (highPos > pos) setHighPos(pos);
    clearSettledSensitivity();
  }

  function handleHighPos(pos: number) {
    setHighPos(Math.min(pos, mediumPos));
    clearSettledSensitivity();
  }

  function handleSaveSensitivity() {
    anomalyMutation.mutate({
      id: rule.id,
      data: { pct_medium: posToPct(mediumPos), pct_high: posToPct(highPos) },
    });
  }

  // Editing after a save/error clears the settled mutation state so a stale
  // "Saved" / "Couldn't save" never lingers next to unsaved sliders.
  function clearSettledSensitivity() {
    if (anomalyMutation.isSuccess || anomalyMutation.isError) anomalyMutation.reset();
  }

  // ML rules expand to their sensitivity editor even without a description,
  // through the same disclosure as every other card.
  const expandable = !!rule.description || (isMlRule && isAdmin);

  return (
    <div className={styles.card}>
      <div
        className={styles.cardView}
        onClick={expandable ? () => setDescExpanded((v) => !v) : undefined}
        role={expandable ? 'button' : undefined}
        tabIndex={expandable ? 0 : undefined}
        aria-expanded={expandable ? descExpanded : undefined}
        onKeyDown={expandable ? (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setDescExpanded((v) => !v); }
        } : undefined}
        style={expandable ? { cursor: 'pointer' } : undefined}
      >
        {expandable && (
          <span
            className={`${styles.discloseCaret} ${descExpanded ? styles.discloseCaretOpen : ''}`}
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </span>
        )}
        <div className={styles.cardLeft}>
          <div className={styles.cardHeader}>
            <span className={styles.cardName}>{rule.name}</span>
            {sqlThreshold?.condition && (
              <span className={styles.cardThreshold}>
                {sqlThreshold.condition.replace('_', ' ')} {sqlThreshold.value}
                {sqlThreshold.unit === 'percent' ? '%' : ''}
              </span>
            )}
          </div>
        </div>
        <div className={styles.cardActions} onClick={(e) => e.stopPropagation()}>
          {canEdit && (
            <button
              className={styles.actionIcon}
              title="Edit"
              onClick={handleEditOpen}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="15" height="15">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
          )}
          {canEdit && (
            <DeleteIconButton
              className={styles.actionIcon}
              title="Delete"
              onClick={() => setIsConfirmOpen(true)}
              size={15}
            />
          )}
          <span className={styles.metaText} style={{ color: borderColor }}>
            {rule.severity}
          </span>
          <span className={styles.metaSep}>|</span>
          <span className={styles.metaText} style={{ color: TYPE_COLORS[rule.type] || '#64748b' }}>
            {TYPE_LABELS[rule.type] ?? rule.type}
          </span>
          <span className={styles.metaSep}>|</span>
          <ToggleSwitch
            checked={rule.enabled}
            onChange={handleToggle}
            disabled={!isAdmin}
          />
        </div>
      </div>

      {descExpanded && rule.description && (
        <div className={styles.cardDescription}>{rule.description}</div>
      )}

      {isMlRule && isAdmin && descExpanded && (
        <div className={styles.mlSensitivityForm}>
          <div className={styles.sensitivityRow}>
            <div className={styles.sensitivityHeading}>Alert sensitivity</div>
            {/* Labels ride above their handle so it's clear which grip sets which tier. */}
            <div className={styles.sensitivityThumbLabels}>
              <span className={styles.sensitivityThumbHigh} style={{ left: `${highPos * 100}%` }}>
                High
              </span>
              <span className={styles.sensitivityThumbMedium} style={{ left: `${mediumPos * 100}%` }}>
                Medium
              </span>
            </div>
            {/* One track, two overlaid range inputs. High zone = [0, high]; Medium zone =
                [high, medium]. Inputs are pointer-events:none except their thumbs (CSS) so
                both handles stay grabbable on the shared track. */}
            <div className={styles.sensitivityTrack}>
              <div
                className={`${styles.sensitivityZone} ${styles.sensitivityZoneHigh}`}
                style={{ left: 0, width: `${highPos * 100}%` }}
              />
              <div
                className={`${styles.sensitivityZone} ${styles.sensitivityZoneMedium}`}
                style={{ left: `${highPos * 100}%`, width: `${(mediumPos - highPos) * 100}%` }}
              />
              <input
                type="range"
                className={styles.sensitivitySlider}
                min={0}
                max={1}
                step={0.01}
                value={highPos}
                // When the handles coincide, lift High above Medium so it stays grabbable
                // and can be dragged back toward stricter (it can only move left from here).
                style={highPos >= mediumPos ? { zIndex: 4 } : undefined}
                onChange={(e) => handleHighPos(parseFloat(e.target.value))}
                aria-label="High alert sensitivity"
              />
              <input
                type="range"
                className={styles.sensitivitySlider}
                min={0}
                max={1}
                step={0.01}
                value={mediumPos}
                onChange={(e) => handleMediumPos(parseFloat(e.target.value))}
                aria-label="Medium alert sensitivity"
              />
            </div>
            <div className={styles.sensitivityEnds}>
              <span>Only the most unusual</span>
              <span>Flag anything slightly odd</span>
            </div>
            <div className={styles.sensitivityCaption}>
              <span className={styles.sensitivityCaptionHigh}>High</span> alerts on{' '}
              {sensitivityPhrase(highPos)}.{' '}
              <span className={styles.sensitivityCaptionMedium}>Medium</span> alerts on{' '}
              {sensitivityPhrase(mediumPos)}.
            </div>
          </div>
          <div className={styles.editActions}>
            {anomalyMutation.isError && (
              <span className={styles.cardThreshold}>Couldn't save. Try again.</span>
            )}
            {/* Success is the button's own green flash rather than a
                neighbouring label — same feedback as every other save in the
                app. The mutation's data is a fresh object per response, so
                its identity change is what restarts the flash; a boolean
                would only fire once. clearSettledSensitivity() resets the
                mutation when a slider moves, which clears it too. */}
            <Button
              variant="primary"
              size="sm"
              onClick={handleSaveSensitivity}
              loading={anomalyMutation.isPending}
              success={anomalyMutation.data ?? null}
              successLabel="Saved"
            >
              Save sensitivity
            </Button>
          </div>
        </div>
      )}

      <ConfirmDialog
        isOpen={isConfirmOpen}
        title="Delete Alert Rule"
        message={`Are you sure you want to delete "${rule.name}"?`}
        onConfirm={handleDelete}
        onCancel={() => setIsConfirmOpen(false)}
      />

      {isEditing && (
        <div className={styles.editForm}>
          <div className={styles.editRow}>
            <label className={styles.editLabel}>Name</label>
            <input
              className={styles.editInput}
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
            />
          </div>
          <div className={styles.editRow}>
            <label className={styles.editLabel}>Description</label>
            <textarea
              className={styles.editInput}
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
            />
          </div>
          <div className={styles.editRow}>
            <label className={styles.editLabel}>Category</label>
            <select
              className={styles.editInput}
              value={editType}
              onChange={(e) => setEditType(e.target.value as AlertRule['type'])}
            >
              <option value="security">Security</option>
              <option value="compliance">Compliance</option>
              <option value="operational">Operational</option>
              <option value="data_quality">Data Quality</option>
            </select>
          </div>
          <div className={styles.editRow}>
            <label className={styles.editLabel}>Severity</label>
            <select
              className={styles.editInput}
              value={editSeverity}
              onChange={(e) => setEditSeverity(e.target.value as AlertRule['severity'])}
            >
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
          <div className={styles.editRow}>
            <label className={styles.editLabel}>Silence Duration (minutes)</label>
            <input
              className={styles.editInput}
              type="number"
              min={0}
              value={editSilence ?? ''}
              onChange={(e) => {
                const v = e.target.value;
                setEditSilence(v === '' ? null : Number(v));
              }}
            />
          </div>
          <div className={styles.editRow}>
            <label className={styles.editLabel}>Targets & Criteria</label>
            <TargetFilters
              ruleType={editType === 'compliance' ? 'compliance' : 'classification'}
              mode={editType === 'compliance' ? 'all' : 'targets-only'}
              filters={editFilters}
              onChange={setEditFilters}
              complianceCriteria={editCriteria}
              onComplianceCriteriaChange={setEditCriteria}
            />
          </div>
          <div className={styles.editRow}>
            <label className={styles.editLabel}>Threshold</label>
            <ThresholdEditor value={editThreshold} onChange={setEditThreshold} />
          </div>
          <div className={styles.editActions}>
            <Button variant="secondary" size="sm" onClick={handleEditCancel} disabled={updateMutation.isPending}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={handleSave} loading={updateMutation.isPending} disabled={!canSaveEdit}>
              Save Changes
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
