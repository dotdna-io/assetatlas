// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState } from 'react';
import { CategoryTag } from '@/components/ui/CategoryTag';
import { Button } from '@/components/ui/Button';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import type { PostureCategory, PostureSubRule, FilterCondition } from '@/api/posture-rules';
import {
  useReorderPostureSubRules,
  useTogglePostureSubRule,
} from '@/api/posture-rules';
import { PostureSubRuleRow } from './PostureSubRuleRow';
import { PostureSubRuleWizard } from './PostureSubRuleWizard';
import styles from './posture.module.css';

interface Props {
  category: PostureCategory;
}

const CAT_ORDER = ['server', 'workstation', 'mobile', 'network', 'iot', 'ot', 'miscellaneous'];

function isCategoryFilter(f: FilterCondition): boolean {
  return f.field_id.kind === 'static' && f.field_id.name.toLowerCase() === 'category';
}

/**
 * Which asset categories the category's rules actually target, derived from
 * each sub-rule's Category target filter:
 *   - `in` / `equals` / `any` (with values) → those categories
 *   - `notempty` / no Category filter (targets every asset) → all categories
 *   - exclusion ops (`notin` / `none` …) → treated as all (applies broadly)
 * `all` wins over a specific set. Drives both the header chips and the
 * coverage-by-category rows. Replaces the retired `applicable_categories`.
 */
function deriveTargetCategories(subRules: PostureSubRule[]): { all: boolean; cats: string[] } {
  const set = new Set<string>();
  let all = false;
  for (const sr of subRules) {
    const catFilters = sr.target_filters.filter(isCategoryFilter);
    if (sr.target_filters.length === 0 || catFilters.length === 0) {
      all = true;
      continue;
    }
    for (const f of catFilters) {
      const op = f.operator.toLowerCase().replace(/[\s_]/g, '');
      const vals = (Array.isArray(f.value) ? f.value : String(f.value).split(','))
        .map((v) => v.trim().toLowerCase())
        .filter(Boolean);
      if (op === 'notempty' || op === 'empty' || vals.length === 0) {
        all = true;
      } else if (op === 'notin' || op === 'none' || op === 'notequals' || op === 'isnot') {
        all = true;
      } else {
        vals.forEach((v) => set.add(v));
      }
    }
  }
  const rank = (c: string) => {
    const i = CAT_ORDER.indexOf(c);
    return i === -1 ? CAT_ORDER.length : i;
  };
  const cats = [...set].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  return { all, cats };
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={16}
      height={16}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`${styles.chevron} ${expanded ? styles.chevronExpanded : ''}`}
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

export function PostureCategoryCard({ category }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [editingSubRule, setEditingSubRule] = useState<PostureSubRule | undefined>(undefined);

  const reorder = useReorderPostureSubRules();
  const toggleSubRule = useTogglePostureSubRule();

  const ruleCount = category.sub_rules.length;
  // Category-level toggle is a derived view of its sub-rules: ON when any
  // sub-rule is enabled. Clicking flips every sub-rule in the category to
  // the opposite of that derived state. There's no category-level enabled
  // field on the backend, so this cascade is the closest "enable / disable
  // the whole posture category" gesture available without a schema change.
  const anyEnabled = category.sub_rules.some((sr) => sr.enabled);

  function handleCategoryToggle(next: boolean) {
    for (const sr of category.sub_rules) {
      if (sr.enabled !== next) {
        toggleSubRule.mutate({ subRuleId: sr.id, enabled: next });
      }
    }
  }

  // Asset categories the rules target (from their Category target filters).
  const { all: targetsAll, cats: targetCats } = useMemo(
    () => deriveTargetCategories(category.sub_rules),
    [category.sub_rules],
  );
  // "Targets all" → empty set, which the breakdown filter reads as "show every
  // category"; otherwise constrain to the targeted categories.
  const applicableSet = useMemo(
    () => (targetsAll ? new Set<string>() : new Set(targetCats)),
    [targetsAll, targetCats],
  );

  // Filter breakdown rows: show categories that are either targeted (even at
  // 0% — a legitimate coverage gap) or have non-zero coverage (honest reporting).
  const breakdownRows = Object.entries(category.stats.by_asset_category ?? {})
    .filter(([, v]) => v.total > 0)
    .filter(([assetCat, v]) => {
      if (applicableSet.size === 0) return true;
      return applicableSet.has(assetCat.toLowerCase()) || v.covered > 0;
    });

  function handleEdit(subRule: PostureSubRule) {
    setEditingSubRule(subRule);
    setWizardOpen(true);
    setExpanded(true);
  }

  function handleAdd() {
    setEditingSubRule(undefined);
    setWizardOpen(true);
    setExpanded(true);
  }

  function handleWizardClose() {
    setWizardOpen(false);
    setEditingSubRule(undefined);
  }

  return (
    <div className={styles.categoryCard}>
      <div
        className={styles.categoryHeader}
        onClick={() => setExpanded((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded((v) => !v); }}
      >
        <div className={styles.categoryMeta}>
          <span className={styles.categoryName}>{category.name}</span>
          <span className={styles.categoryCount}>
            {ruleCount} {ruleCount === 1 ? 'rule' : 'rules'}
          </span>
        </div>
        {/* Read-only icons of the categories the rules target (from their
            Category target filters). All category icons when a rule targets
            every category (e.g. "Category is not empty"). */}
        <div className={styles.assetBadgeRow}>
          {(targetsAll ? CAT_ORDER : targetCats).map((cat) => (
            <CategoryTag key={cat} category={cat} iconOnly />
          ))}
        </div>
        <span className={styles.headerDivider} aria-hidden />
        <div className={styles.categoryActions} onClick={(e) => e.stopPropagation()}>
          <span className={styles.metaText} style={{ color: '#3b82f6' }}>Posture</span>
          <span className={styles.metaSep}>|</span>
          <ToggleSwitch
            checked={anyEnabled}
            onChange={handleCategoryToggle}
            disabled={ruleCount === 0 || toggleSubRule.isPending}
          />
        </div>
        <ChevronIcon expanded={expanded} />
      </div>

      {expanded && (
        <>
          {category.description && (
            <p className={styles.categoryDesc}>{category.description}</p>
          )}

          {/* Rules — directly below the description. */}
          <div className={styles.subRulesTitle}>Rules</div>
          <div className={styles.subRules}>
            {ruleCount === 0 ? (
              <p className={styles.emptySubRules}>
                No rules configured — all assets show N/A for this category.
              </p>
            ) : (
              category.sub_rules.map((sr, i) => (
                <PostureSubRuleRow
                  key={sr.id}
                  subRule={sr}
                  onEdit={handleEdit}
                  canMoveUp={i > 0}
                  canMoveDown={i < category.sub_rules.length - 1}
                  onMoveUp={() => {
                    const ids = category.sub_rules.map((r) => r.id);
                    [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]];
                    reorder.mutate({ categoryId: category.id, orderedIds: ids });
                  }}
                  onMoveDown={() => {
                    const ids = category.sub_rules.map((r) => r.id);
                    [ids[i], ids[i + 1]] = [ids[i + 1], ids[i]];
                    reorder.mutate({ categoryId: category.id, orderedIds: ids });
                  }}
                />
              ))
            )}
          </div>

          {/* Hide "New Rule" while the wizard is open so the user isn't
              presented with two competing action zones. */}
          {!wizardOpen && (
            <div className={styles.categoryFooter}>
              <Button variant="primary" onClick={handleAdd}>
                New Rule
              </Button>
            </div>
          )}

          {/* Coverage by asset category — full width, below Rules. covered =
              assets matching the criteria (compliant); total = all assets in
              the category. */}
          {breakdownRows.length > 0 && (
            <div className={styles.coverageBlock}>
              <div className={styles.coverageBreakdownTitle}>
                Coverage by asset category
              </div>
              <div className={styles.coverageBreakdownRows}>
                {breakdownRows.map(([assetCat, v]) => {
                  const pct = v.total > 0 ? (v.covered / v.total) * 100 : 0;
                  return (
                    <div key={assetCat} className={styles.coverageBreakdownRow}>
                      <span className={styles.coverageBreakdownLabel}>{assetCat}</span>
                      <div className={styles.coverageBreakdownBar}>
                        <div
                          className={styles.coverageBreakdownFill}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className={styles.coverageBreakdownCount}>
                        {v.covered.toLocaleString()} / {v.total.toLocaleString()}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <PostureSubRuleWizard
            isOpen={wizardOpen}
            categoryId={category.id}
            existing={editingSubRule}
            onClose={handleWizardClose}
          />
        </>
      )}
    </div>
  );
}
