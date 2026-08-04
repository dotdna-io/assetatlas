// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import {
  useAssetLabelOverride,
  useSetAssetLabelOverride,
  useClearAssetLabelOverride,
} from '@/api/assets';
import { useAssetDetail, type AssetDetail } from '@/api/asset-details';
import styles from './edit.module.css';

interface AssetLabelTabProps {
  assetId: string;
  onSuccess: () => void;
}

const FIELDS_IN_ORDER: { key: keyof AssetDetail; label: string }[] = [
  { key: 'hostname', label: 'hostname' },
  { key: 'fqdn', label: 'FQDN' },
  { key: 'mac', label: 'MAC' },
  { key: 'sn', label: 'serial' },
  { key: 'ip', label: 'IP' },
];

function firstString(value: string | string[] | null): string | null {
  if (Array.isArray(value)) {
    for (const v of value) {
      if (v.trim() !== '') return v;
    }
    return null;
  }
  return value && value.trim() !== '' ? value : null;
}

function deriveComputedSource(asset?: AssetDetail): {
  computed: string | null;
  source: string | null;
} {
  if (!asset) return { computed: null, source: null };
  for (const { key, label } of FIELDS_IN_ORDER) {
    const v = firstString(asset[key] as string | string[] | null);
    if (v) return { computed: v, source: label };
  }
  return { computed: null, source: null };
}

export function AssetLabelTab({ assetId, onSuccess }: AssetLabelTabProps) {
  const [status, setStatus] = useInlineStatus();

  // Fetch the override and the asset detail directly. The parent modal
  // doesn't always pass asset data, and `asset_label_override` isn't part
  // of the standard asset detail payload — so we resolve both here.
  const { data: overrideData, isLoading: overrideLoading } = useAssetLabelOverride(assetId);
  const { data: detailData, isLoading: detailLoading } = useAssetDetail(assetId);
  const setMutation = useSetAssetLabelOverride();
  const clearMutation = useClearAssetLabelOverride();

  const currentOverride =
    typeof overrideData?.label === 'string' && overrideData.label
      ? overrideData.label
      : null;

  const { computed, source } = deriveComputedSource(detailData?.asset);

  const [draft, setDraft] = useState<string>('');

  useEffect(() => {
    setDraft(currentOverride ?? '');
  }, [assetId, currentOverride]);

  const handleSave = () => {
    const value = draft.trim();
    if (!value) {
      setStatus({ kind: 'error', message: 'Label cannot be empty. Use Clear to remove the override.' });
      return;
    }
    setMutation.mutate(
      { id: assetId, data: { label: value } },
      {
        onSuccess: () => {
          setStatus({ kind: 'success', message: 'Override saved', action: 'save' });
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Failed to save override: ' + err.message });
        },
      },
    );
  };

  const handleClear = () => {
    clearMutation.mutate(
      { id: assetId },
      {
        onSuccess: () => {
          setStatus({ kind: 'success', message: 'Override cleared', action: 'clear' });
          setDraft('');
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Failed to clear override: ' + err.message });
        },
      },
    );
  };

  const labelSourceCaption = currentOverride
    ? 'from manual override'
    : source
    ? `from ${source}`
    : 'no underlying value available';

  const displayedLabel = currentOverride ?? computed ?? '—';

  return (
    <div className={styles.tabContent}>
      <InlineStatus status={status} />

      <div className={styles.formRow}>
        <label className={styles.formLabel}>Currently displayed as</label>
        <div>
          {overrideLoading || detailLoading ? (
            <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-size-sm)' }}>
              Loading…
            </span>
          ) : (
            <>
              <strong>{displayedLabel}</strong>{' '}
              <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-size-sm)' }}>
                ({labelSourceCaption})
              </span>
            </>
          )}
        </div>
      </div>

      <div className={styles.formRow}>
        <label className={styles.formLabel}>Override label</label>
        <input
          type="text"
          className={styles.textInput}
          placeholder="Custom label..."
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      </div>

      <div className={styles.formRow}>
        <Button
          variant="primary"
          size="sm"
          onClick={handleSave}
          loading={setMutation.isPending}
          disabled={!draft.trim() || draft.trim() === currentOverride}
          success={successFor(status, 'save')}
          successLabel="Saved"
        >
          Save override
        </Button>{' '}
        <Button
          variant="secondary"
          size="sm"
          onClick={handleClear}
          loading={clearMutation.isPending}
          disabled={!currentOverride}
          success={successFor(status, 'clear')}
        >
          Clear override
        </Button>
      </div>
    </div>
  );
}
