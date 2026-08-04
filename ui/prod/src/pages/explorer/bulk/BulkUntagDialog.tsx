// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect } from 'react';
import { useTagBulkCandidates, useUntagBulk } from '@/api/assets';
import type { AssetFilter } from '@/api/assets';
import { getApiErrorMessage } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from '../edit/edit.module.css';

interface Props {
  filters: unknown[];
  search?: string;
  matchCount: number;
  onClose: () => void;
}

export function BulkUntagDialog({ filters, search, matchCount, onClose }: Props) {
  const [status, setStatus] = useInlineStatus(0); // manual clear — stays until dismissed or close
  const [tagName, setTagName] = useState('');
  const untagBulk = useUntagBulk();

  // The modal mounts fresh on every open, so the picker loads on mount with no
  // refetch plumbing. Only manual tags come back: assets.tags is the union of
  // the rule-applied rows and the manual ones, and only manual rows can be
  // deleted — offering a rule-based tag would mean a removal that reports zero
  // and a tag the next pipeline run re-applies anyway.
  const candidatesQuery = useTagBulkCandidates({ filters, search });
  const candidates = candidatesQuery.data?.candidates ?? [];
  const loading = candidatesQuery.isLoading;

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

  // Surface the server's actionable message — the candidates load enforces the
  // same cap as the removal, so "Filter matches more than 1000 assets…" can
  // arrive here rather than on the action, and either way it beats the
  // debug-style "Request failed: … (400)" string on ApiError.message.
  const loadError = candidatesQuery.error
    ? getApiErrorMessage(candidatesQuery.error, 'Could not load tags')
    : null;
  const shownStatus =
    status ?? (loadError ? ({ kind: 'error', message: loadError } as const) : null);

  // Carriers, not matches: only the assets holding the tag change, so promising
  // the match count would overstate what the removal does.
  const carrierCount = candidates.find((c) => c.tag_name === tagName)?.count ?? 0;

  const handleRemove = () => {
    untagBulk.mutate(
      {
        filters: filters as AssetFilter[],
        search: search || undefined,
        tag_name: tagName,
      },
      {
        onSuccess: (res) => {
          setStatus({
            kind: 'success',
            message: `Removed "${tagName}" from ${res.removed} assets`,
          });
          // Drop the selection: the invalidation refetches the candidates
          // without the removed tag, and a value with no matching option
          // leaves the select blank while the confirm stays enabled reading
          // "Remove from 0" — one more click in the close delay would fire a
          // second, guaranteed no-op removal.
          setTagName('');
          scheduleClose();
        },
        onError: (err: Error) =>
          setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Operation failed') }),
      },
    );
  };

  const handleCancel = () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    onClose();
  };

  const placeholder = loading
    ? 'Loading tags...'
    : loadError
      ? 'Tags unavailable'
      : candidates.length === 0
        ? 'No manual tags'
        : 'Select a tag...';

  return (
    <div className={styles.tabContent}>
      <InlineStatus status={shownStatus} />
      <p>
        This will remove one manual tag from the <strong>{matchCount} matching assets</strong>.
        This affects every asset matching the current filter, not just the visible rows. Assets
        that don't carry the tag are left alone.
      </p>
      {!loading && !loadError && candidates.length === 0 && (
        <p>No manual tags on the matching assets — rule-based tags cannot be removed here.</p>
      )}
      <div className={styles.formRow}>
        <label className={styles.formLabel} htmlFor="bulk-untag-tag">
          Tag
        </label>
        <select
          id="bulk-untag-tag"
          className={styles.selectInput}
          value={tagName}
          onChange={(e) => setTagName(e.target.value)}
          disabled={loading || candidates.length === 0 || untagBulk.isPending}
        >
          <option value="">{placeholder}</option>
          {candidates.map((c) => (
            <option key={c.tag_name} value={c.tag_name}>
              {c.tag_name} ({c.count})
            </option>
          ))}
        </select>
      </div>
      <div className={styles.bulkActionsRow}>
        <Button
          variant="primary"
          size="sm"
          onClick={handleRemove}
          loading={untagBulk.isPending}
          disabled={!tagName || untagBulk.isPending}
        >
          Remove from {carrierCount}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleCancel}
          disabled={untagBulk.isPending}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
