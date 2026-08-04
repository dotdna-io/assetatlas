// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect } from 'react';
import { useClassifyBulk, useClearClassifyBulk } from '@/api/assets';
import type { AssetFilter } from '@/api/assets';
import { getApiErrorMessage } from '@/api/client';
import { CategorySubcategorySelect } from '../edit/CategorySubcategorySelect';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus, successFor } from '@/components/ui/InlineStatus';
import styles from '../edit/edit.module.css';

interface Props {
  mode: 'recategorize' | 'clear';
  filters: unknown[];
  search?: string;
  matchCount: number;
  onClose: () => void;
}

export function BulkRecategorizeDialog({ mode, filters, search, matchCount, onClose }: Props) {
  const [status, setStatus] = useInlineStatus(0); // manual clear — stays until dismissed or close
  const [category, setCategory] = useState('');
  const [subcategory, setSubcategory] = useState('');
  const classifyBulk = useClassifyBulk();
  const clearBulk = useClearClassifyBulk();

  // How many rows the server actually touched, carried onto the button's
  // success label. An `action`-tagged success renders screen-reader-only —
  // the clicked button owns the visible confirmation — so the count has to
  // ride on the button or it is not seen at all. It cannot come from
  // matchCount: that is the pre-flight estimate, and the server re-evaluates
  // the filter, so the two differ exactly when the difference matters.
  const [settledCount, setSettledCount] = useState<number | null>(null);

  // Delay before the modal closes, so the success count and the button's green
  // flash are both seen. Matches the Button's own flash duration — at the
  // previous 900ms the dialog vanished part-way through the animation, which
  // read as a glitch rather than confirmation.
  const CLOSE_DELAY_MS = 1500;

  // Tracked in a ref so we can clear it on unmount / early close and avoid a
  // dangling timer firing onClose after the component is gone.
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    };
  }, []);

  const scheduleClose = () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(onClose, CLOSE_DELAY_MS);
  };

  // Surface the server's actionable message (e.g. "Filter matches more than
  // 1000 assets. Narrow your filter…") rather than the debug-style
  // "Request failed: POST … (400)" string on ApiError.message.
  const onError = (err: Error) =>
    setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Operation failed') });

  const handleRecategorize = () => {
    if (!category || !subcategory) {
      setStatus({ kind: 'error', message: 'Both category and subcategory are required' });
      return;
    }
    classifyBulk.mutate(
      { filters: filters as AssetFilter[], search: search || undefined, category, subcategory },
      {
        onSuccess: (res) => {
          setSettledCount(res.updated);
          setStatus({
            kind: 'success',
            message: `Recategorized ${res.updated} assets`,
            action: 'recategorize',
          });
          scheduleClose();
        },
        onError,
      },
    );
  };

  const handleClear = () => {
    clearBulk.mutate(
      { filters: filters as AssetFilter[], search: search || undefined },
      {
        onSuccess: (res) => {
          setSettledCount(res.cleared);
          setStatus({
            kind: 'success',
            message: `Cleared overrides for ${res.cleared} assets`,
            action: 'clear',
          });
          scheduleClose();
        },
        onError,
      },
    );
  };

  const handleCancel = () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    onClose();
  };

  const pending = classifyBulk.isPending || clearBulk.isPending;

  return (
    <div className={styles.tabContent}>
      <InlineStatus status={status} />
      <p>
        This will{' '}
        {mode === 'recategorize'
          ? 'set the category/subcategory for'
          : 'reset category overrides for'}{' '}
        <strong>{matchCount} matching assets</strong>. This affects every asset matching the
        current filter, not just the visible rows.
      </p>
      {mode === 'recategorize' && (
        <CategorySubcategorySelect
          category={category}
          subcategory={subcategory}
          onCategoryChange={(cat) => {
            setCategory(cat);
            setSubcategory('');
          }}
          onSubcategoryChange={setSubcategory}
          disabled={pending}
        />
      )}
      <div className={styles.bulkActionsRow}>
        {mode === 'recategorize' ? (
          <Button
            variant="primary"
            size="sm"
            onClick={handleRecategorize}
            loading={classifyBulk.isPending}
            disabled={!category || !subcategory || pending}
            success={successFor(status, 'recategorize')}
            successLabel={settledCount === null ? 'Recategorized' : `Recategorized ${settledCount}`}
          >
            Recategorize {matchCount}
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={handleClear}
            loading={clearBulk.isPending}
            disabled={pending}
            success={successFor(status, 'clear')}
            successLabel={settledCount === null ? 'Reset' : `Reset ${settledCount}`}
          >
            Reset {matchCount} to predicted
          </Button>
        )}
        <Button variant="secondary" size="sm" onClick={handleCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
