// SPDX-License-Identifier: AGPL-3.0-or-later
import { Fragment, useMemo } from 'react';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { DeleteIconButton } from '@/components/ui/IconButton';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { useTogglePostureSubRule, useDeletePostureSubRule } from '@/api/posture-rules';
import type { PostureSubRule, FilterCondition } from '@/api/posture-rules';
import { useTagRuleFields } from '@/api/tag-rules';
import type { FieldCatalogEntry, FieldId } from '@/types/field-id';
import { encodeFieldId } from '@/types/field-id';
import { buildFieldMap } from '@/pages/explorer/explorer-utils';
import { operatorLabel } from '@/components/ui/FilterRow';
import { formatRelativeTime } from '@/pages/integrations/integration-utils';
import styles from './posture.module.css';

interface Props {
  subRule: PostureSubRule;
  onEdit: (subRule: PostureSubRule) => void;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
}

// Resolve a FieldId to its human-readable display_name via the catalog,
// falling back to the canonical encoded form if the field is unknown.
function fieldLabel(fieldId: FieldId, lookup: Record<string, FieldCatalogEntry>): string {
  const encoded = encodeFieldId(fieldId);
  return lookup[encoded]?.display_name ?? encoded;
}

function conditionText(c: FilterCondition, lookup: Record<string, FieldCatalogEntry>): string {
  const entry = lookup[encodeFieldId(c.field_id)];
  // List/range values are arrays now; render them as a comma-joined display string.
  const valueText = Array.isArray(c.value) ? c.value.join(', ') : c.value;
  return `${fieldLabel(c.field_id, lookup)} ${operatorLabel(c.operator, entry)} ${valueText}`;
}

export function PostureSubRuleRow({ subRule, onEdit, onMoveUp, onMoveDown, canMoveUp, canMoveDown }: Props) {
  const toggle = useTogglePostureSubRule();
  const remove = useDeletePostureSubRule();
  const [status, setStatus] = useInlineStatus();
  // react-query caches this globally, so sibling rows share one fetch.
  const { data: fieldsData } = useTagRuleFields();
  const fieldLookup = useMemo(
    () => buildFieldMap(fieldsData?.entries),
    [fieldsData],
  );

  function handleToggle(enabled: boolean) {
    toggle.mutate(
      { subRuleId: subRule.id, enabled },
      {
        onSuccess: () => setStatus(null), // the row's switch shows the new state
        onError: (err) => setStatus({ kind: 'error', message: err.message || 'Failed to toggle rule' }),
      },
    );
  }

  function handleDelete() {
    if (!window.confirm(`Delete rule "${subRule.name}"? This cannot be undone.`)) return;
    remove.mutate(subRule.id, {
      onSuccess: () => setStatus(null), // the row leaves the category
      onError: (err) => setStatus({ kind: 'error', message: err.message || 'Failed to delete rule' }),
    });
  }

  const criteriaSep = subRule.compliance_criteria.logic === 'all' ? 'AND' : 'OR';
  // Output is a JSONB dict: {mode, field_id} or {mode, text}.
  const outputDisplay: string | null = subRule.output_value
    ? subRule.output_value.mode === 'text'
      ? subRule.output_value.text
      : fieldLabel(subRule.output_value.field_id, fieldLookup)
    : null;

  return (
    <div className={styles.subRuleRow}>
      {/* Reorder controls on the left — sub-rules execute top-down,
          so position is meaningful. Stacked arrows make the up/down
          relationship spatially obvious. */}
      <div className={styles.reorderControls}>
        <button
          type="button"
          className={styles.reorderBtn}
          onClick={onMoveUp}
          disabled={!canMoveUp || !onMoveUp}
          aria-label="Move up"
          title="Move up"
        >
          <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </button>
        <button
          type="button"
          className={styles.reorderBtn}
          onClick={onMoveDown}
          disabled={!canMoveDown || !onMoveDown}
          aria-label="Move down"
          title="Move down"
        >
          <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>

      <div className={styles.subRuleInfo}>
        <span className={styles.subRuleName}>{subRule.name}</span>
        {/* Each filter / criteria condition / output gets its own chip
            so the rule's individual sub-checks are all visible (no
            joined-string truncation). Group labels and AND/OR text
            keep the boolean structure readable. */}
        <div className={styles.subRuleChips}>
          <span className={styles.chipGroupLabel}>Target:</span>
          {subRule.target_filters.length === 0 ? (
            <span className={styles.chipMuted}>no filters</span>
          ) : (
            subRule.target_filters.map((f, i) => (
              <Fragment key={`tf-${i}`}>
                {i > 0 && <span className={styles.chipSep}>AND</span>}
                <span className={styles.chip}>{conditionText(f, fieldLookup)}</span>
              </Fragment>
            ))
          )}
          <span className={styles.chipSep}>→</span>
          <span className={styles.chipGroupLabel}>
            Criteria ({subRule.compliance_criteria.logic}):
          </span>
          {subRule.compliance_criteria.conditions.length === 0 ? (
            <span className={styles.chipMuted}>no conditions</span>
          ) : (
            subRule.compliance_criteria.conditions.map((c, i) => (
              <Fragment key={`cc-${i}`}>
                {i > 0 && <span className={styles.chipSep}>{criteriaSep}</span>}
                <span className={styles.chip}>{conditionText(c, fieldLookup)}</span>
              </Fragment>
            ))
          )}
          {subRule.output_mode && outputDisplay && (
            <>
              <span className={styles.chipSep}>→</span>
              <span className={styles.chipGroupLabel}>
                {subRule.output_mode === 'text' ? 'Output:' : 'Output field:'}
              </span>
              <span className={styles.chip}>{outputDisplay}</span>
            </>
          )}
          {subRule.last_edit && (
            <span className={styles.lastEditChip}>
              Edited {formatRelativeTime(subRule.last_edit.at)}
              {' by '}
              {subRule.last_edit.changed_by_username || subRule.last_edit.changed_by}
            </span>
          )}
        </div>
      </div>

      <div className={styles.subRuleActions}>
        <InlineStatus status={status} align="inline" />
        <button
          type="button"
          className={styles.iconBtn}
          onClick={() => onEdit(subRule)}
          aria-label="Edit rule"
          title="Edit rule"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
          </svg>
        </button>
        <DeleteIconButton
          className={styles.iconBtn}
          onClick={handleDelete}
          aria-label="Delete rule"
          title="Delete rule"
          disabled={remove.isPending}
          size={15}
        />
        <ToggleSwitch
          checked={subRule.enabled}
          onChange={handleToggle}
        />
      </div>
    </div>
  );
}
