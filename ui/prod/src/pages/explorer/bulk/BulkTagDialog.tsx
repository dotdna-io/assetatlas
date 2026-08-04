// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect } from 'react';
import { useTagBulk } from '@/api/assets';
import type { AssetFilter } from '@/api/assets';
import { getApiErrorMessage } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { ColorPicker } from '@/components/ui/ColorPicker/ColorPicker';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from '../edit/edit.module.css';

interface Props {
  filters: unknown[];
  search?: string;
  matchCount: number;
  onClose: () => void;
}

// Same starting swatch the per-asset tag tab offers, so a tag created either
// way looks the same by default.
const DEFAULT_COLOR = '#3b82f6';

export function BulkTagDialog({ filters, search, matchCount, onClose }: Props) {
  const [status, setStatus] = useInlineStatus(0); // manual clear — stays until dismissed or close
  const [tagName, setTagName] = useState('');
  const [tagColor, setTagColor] = useState(DEFAULT_COLOR);
  const tagBulk = useTagBulk();

  // Brief delay lets the user see the success count before the modal closes.
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
    closeTimerRef.current = setTimeout(onClose, 900);
  };

  const trimmed = tagName.trim();

  const handleTag = () => {
    if (!trimmed) {
      setStatus({ kind: 'error', message: 'Tag name is required' });
      return;
    }
    tagBulk.mutate(
      {
        filters: filters as AssetFilter[],
        search: search || undefined,
        tag_name: trimmed,
        tag_color: tagColor,
      },
      {
        onSuccess: (res) => {
          setStatus({
            kind: 'success',
            message:
              res.created === res.tagged
                ? `Tagged ${res.tagged} assets`
                : `Tagged ${res.tagged} assets (${res.created} new)`,
          });
          scheduleClose();
        },
        // Surface the server's actionable message (e.g. "Filter matches more
        // than 1000 assets. Narrow your filter…") rather than the debug-style
        // "Request failed: POST … (400)" string on ApiError.message.
        onError: (err: Error) =>
          setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Operation failed') }),
      },
    );
  };

  const handleCancel = () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    onClose();
  };

  return (
    <div className={styles.tabContent}>
      <InlineStatus status={status} />
      <p>
        This will add one manual tag to <strong>{matchCount} matching assets</strong>. This
        affects every asset matching the current filter, not just the visible rows. Assets that
        already carry the tag keep it and take the colour below.
      </p>
      <div className={styles.formRow}>
        <label className={styles.formLabel} htmlFor="bulk-tag-name">
          Tag Name
        </label>
        <input
          id="bulk-tag-name"
          type="text"
          className={styles.textInput}
          placeholder="Tag name..."
          value={tagName}
          onChange={(e) => setTagName(e.target.value)}
          maxLength={100}
          disabled={tagBulk.isPending}
        />
      </div>
      <div className={styles.formRow}>
        <label className={styles.formLabel}>Tag Color</label>
        <ColorPicker value={tagColor} onChange={setTagColor} />
      </div>
      <div className={styles.bulkActionsRow}>
        <Button
          variant="primary"
          size="sm"
          onClick={handleTag}
          loading={tagBulk.isPending}
          disabled={!trimmed || tagBulk.isPending}
        >
          Tag {matchCount}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleCancel}
          disabled={tagBulk.isPending}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
