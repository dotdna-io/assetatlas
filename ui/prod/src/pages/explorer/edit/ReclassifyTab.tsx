// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect } from 'react';
import {
  useClassifyAsset,
  useCategoryOverride,
  useClearCategoryOverride,
  type CategoryOverrideResponse,
} from '@/api/assets';
import { Button } from '@/components/ui/Button';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import { CategorySubcategorySelect } from './CategorySubcategorySelect';
import styles from './edit.module.css';

export { ADD_SUBCATEGORY_SENTINEL } from './CategorySubcategorySelect';

interface ReclassifyTabProps {
  assetId: string;
  currentCategory?: string;
  currentSubcategory?: string;
  onSuccess: () => void;
}

/** Local calendar date, matching MergeTab's merge-history stamps. */
function formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** "Server → Linux", or just the category when only that was predicted.
 *  Null when there is nothing to show: the two columns are empty on assets last
 *  written before migration 143, and naming a prediction that isn't there would
 *  promise a restore that leaves the values exactly as they are. */
function formatPredicted(override: CategoryOverrideResponse | undefined): string | null {
  const category = override?.category_predicted;
  if (!category) return null;
  const subcategory = override?.subcategory_predicted;
  return subcategory ? `${category} → ${subcategory}` : category;
}

/** Who and when, in MergeTab's phrasing. Either half can be missing —
 *  `overridden_by` and `overridden_at` are both nullable columns. */
function formatAttribution(override: CategoryOverrideResponse): string {
  const when = formatDate(override.overridden_at);
  const who = override.overridden_by;
  if (when && who) return `${when} by ${who}`;
  return when || (who ? `by ${who}` : '');
}

export function ReclassifyTab({
  assetId,
  currentCategory,
  currentSubcategory,
  onSuccess,
}: ReclassifyTabProps) {
  const [status, setStatus] = useInlineStatus();
  const classifyMutation = useClassifyAsset();
  const [selectedCategory, setSelectedCategory] = useState(currentCategory ?? '');
  const [selectedSubcategory, setSelectedSubcategory] = useState(currentSubcategory ?? '');

  // Whether the values on screen came from the pipeline or from someone's
  // manual correction — answered from `category_overrides`, which is why this is
  // a request of its own rather than a field on the asset detail (#1155).
  const {
    data: override,
    isLoading: overrideLoading,
    isError: overrideFailed,
  } = useCategoryOverride(assetId);
  const clearMutation = useClearCategoryOverride();

  useEffect(() => {
    setSelectedCategory(currentCategory ?? '');
    setSelectedSubcategory(currentSubcategory ?? '');
  }, [assetId, currentCategory, currentSubcategory]);

  const handleSave = () => {
    if (!selectedCategory || !selectedSubcategory) {
      setStatus({ kind: 'error', message: 'Both category and subcategory are required' });
      return;
    }

    classifyMutation.mutate(
      { id: assetId, data: { category: selectedCategory, subcategory: selectedSubcategory } },
      {
        onSuccess: () => {
          setStatus({ kind: 'success', message: 'Asset reclassified successfully', action: 'save' });
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Reclassify failed: ' + err.message });
        },
      },
    );
  };

  const handleRestore = () => {
    clearMutation.mutate(
      { id: assetId },
      {
        // No `action` here, deliberately: an action-tagged success hands the
        // confirmation to the clicked Button, and this button lives inside the
        // panel that the refetch removes a moment later — the user would be left
        // with no confirmation at all. The centred flash outlives the panel.
        // `cleared: false` means someone else already cleared it between the
        // read and the click; saying "restored" there is the silent-success
        // shape this endpoint reports a count to avoid.
        onSuccess: (data) => {
          setStatus({
            kind: 'success',
            message: data.cleared
              ? 'Restored to the predicted classification'
              : 'This asset no longer carries an override',
          });
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Restore failed: ' + err.message });
        },
      },
    );
  };

  const predicted = formatPredicted(override);
  const attribution = override ? formatAttribution(override) : '';
  // Nothing extra while the state is in flight: an untouched asset must look
  // exactly as it did before this panel existed, and a placeholder would flash
  // on every asset to say something about most of them that isn't true.
  const showOverride = !overrideLoading && override?.is_overridden === true;

  return (
    <div className={styles.tabContent}>
      <InlineStatus status={status} />

      {/* A failed read renders identically to "never reclassified" unless it
        * says so: the tab would then hide the indicator and the restore control
        * on an asset that has both. The service deliberately lets this read
        * fail rather than degrade to empty (#1155) — dropping the error here
        * would undo that. */}
      {overrideFailed && (
        <div className={styles.overrideNotice}>
          <div className={styles.overrideNoticeMeta}>
            Could not load the reclassification state for this asset — it may carry a
            manual override that is not shown here.
          </div>
        </div>
      )}

      {showOverride && override && (
        <div className={styles.overrideNotice}>
          <div className={styles.overrideNoticeTitle}>Manually reclassified</div>
          {attribution && (
            <div className={styles.overrideNoticeMeta}>{attribution}</div>
          )}
          {override.reason && (
            <div className={styles.overrideNoticeReason}>{override.reason}</div>
          )}
          <div className={styles.overrideNoticeMeta}>
            {predicted ? (
              <>
                Pipeline predicted: <strong>{predicted}</strong>
              </>
            ) : (
              'No stored prediction for this asset — restoring drops the override and the next pipeline run reclassifies it.'
            )}
          </div>
          <div className={styles.overrideNoticeActions}>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleRestore}
              loading={clearMutation.isPending}
            >
              Restore predicted
            </Button>
          </div>
        </div>
      )}

      {/*
       * Remount (resetting the selector's internal add-mode state) when the
       * asset OR its current category/subcategory changes — matches the original
       * useEffect reset on [assetId, currentCategory, currentSubcategory].
       */}
      <CategorySubcategorySelect
        key={`${assetId}:${currentCategory ?? ''}:${currentSubcategory ?? ''}`}
        category={selectedCategory}
        subcategory={selectedSubcategory}
        onCategoryChange={setSelectedCategory}
        onSubcategoryChange={setSelectedSubcategory}
      />
      <Button
        variant="primary"
        size="sm"
        onClick={handleSave}
        loading={classifyMutation.isPending}
        disabled={!selectedCategory || !selectedSubcategory}
        success={successFor(status, 'save')}
        successLabel="Saved"
      >
        Save
      </Button>
    </div>
  );
}
