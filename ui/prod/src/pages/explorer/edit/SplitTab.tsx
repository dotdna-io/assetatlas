// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/explorer/edit/SplitTab.tsx
//
// "Split" tab in the Edit Asset modal (Task 8). Lets an admin confirm a
// preserve-primary split that applies on the next pipeline run.
//
// Two flows, both backed by the same GET /assets/:id/split-preview:
//   - Auto-detect: the pipeline's own bridge-detection result (no `bridge`
//     param). If it would split, show the proposed clusters directly.
//   - Manual override: when auto-detect finds nothing, the tab leads with a
//     "no over-merge" verdict and tucks the merge-key picker (a second,
//     bridge-scoped preview call) under an "Advanced" disclosure — on a
//     correctly-merged asset no single identifier can produce a split, so the
//     picker is opt-in rather than the primary, dead-end UI.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/ui/Button';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import { useSplitPreview, useExecuteSplit, type SplitPreview } from '@/api/asset-split';
import styles from './edit.module.css';

interface SplitTabProps {
  assetId: string;
  assetData?: Record<string, unknown>;
  onSuccess: () => void;
}

export function SplitTab({ assetId, onSuccess }: SplitTabProps) {
  const { isAdmin } = useAuth();
  const [status, setStatus] = useInlineStatus();
  const [primaryIdx, setPrimaryIdx] = useState(0);
  const [manualBridge, setManualBridge] = useState<string | null>(null);

  const auto = useSplitPreview(assetId);
  const manual = useSplitPreview(manualBridge ? assetId : null, manualBridge ?? undefined);

  const split = useExecuteSplit(assetId, {
    onSuccess: (res) => {
      const when = res.next_run_at ? new Date(res.next_run_at).toLocaleString() : 'the next run';
      setStatus({ kind: 'success', message: `Split registered — applies on ${when}.`, action: 'split' });
      onSuccess();
    },
    onError: (e) => setStatus({ kind: 'error', message: 'Split failed: ' + e.message }),
  });

  if (auto.isLoading || !auto.data) return <div className={styles.tabContent}>Loading…</div>;

  // Active preview: the manual one if the admin picked an identifier, else auto-detect.
  const active: SplitPreview | undefined = manualBridge ? manual.data : auto.data;
  const mergeKeys = auto.data.merge_keys;

  const confirm = (p: SplitPreview) =>
    split.mutate({ bridge: [p.bridge as string], primary_cluster: p.clusters[primaryIdx], clusters: p.clusters });

  const nice = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : 'the next run');

  return (
    <div className={styles.tabContent}>
      {status && <InlineStatus status={status} />}

      {active && active.would_split && active.bridge ? (
        <>
          <div className={styles.splitCardTitle}>
            ⚠ Splits into {active.clusters.length} devices by <code>{active.bridge}</code>
          </div>
          {active.warnings.map((w, i) => <div key={i} className={styles.splitWarn}>⚠ {w}</div>)}
          <div className={styles.splitCluster}>
            {active.clusters.map((cluster, idx) => (
              <div key={idx} className={[styles.splitCard, idx === primaryIdx ? styles.splitCardActive : ''].filter(Boolean).join(' ')}>
                <label className={styles.splitCardTitle}>
                  <input type="radio" name="primary" checked={idx === primaryIdx} onChange={() => setPrimaryIdx(idx)} />
                  Keep ID &amp; history
                </label>
                {cluster.map((c) => <div key={c} className={styles.splitComp}>{c}</div>)}
              </div>
            ))}
          </div>
          <div className={styles.splitFoot}>
            <span className={styles.mergeEmptyText}>Distrusts <code>{active.bridge}</code> · applies on {nice(active.next_run_at)}</span>
            {active.warnings.length > 0 && (
              <span className={styles.splitWarn}>Can't split — would orphan a device without a unique identifier</span>
            )}
            {manualBridge && <Button variant="secondary" onClick={() => { setManualBridge(null); setPrimaryIdx(0); }}>Back</Button>}
            <Button variant="danger" onClick={() => confirm(active)} disabled={!isAdmin || (active.warnings?.length ?? 0) > 0} loading={split.isPending} success={successFor(status, 'split')}>Split asset</Button>
          </div>
        </>
      ) : (
        <>
          {/* Reached only when auto-detect found no over-merge: state a plain
              verdict instead of a picker that, by construction, can never
              confirm a split here (see split_detection.detect_bridge). */}
          <div className={styles.splitVerdict}>
            <span className={styles.splitVerdictCheck}>✓</span>
            <span>No over-merge detected — every source for this asset resolves to one device, so there’s nothing to split.</span>
          </div>

          {/* Off-ramp + manual probe are admin-only: only admins manage the
              denylist or confirm a split, and the probe is demoted because on
              a correctly-merged asset every identifier reports "won't
              separate", so it's opt-in, not the default view. */}
          {isAdmin && (
            <>
            <p className={styles.splitOfframp}>
              Certain two devices were wrongly merged? Add the shared identifier to the{' '}
              <Link to="/settings/data/merge-denylist">Merge Denylist</Link> and they’ll separate on the next run.
            </p>

            <details className={styles.splitAdvanced}>
              <summary>Advanced: test a specific identifier</summary>
              <p className={styles.mergeEmptyText}>
                Pick an identifier to check whether distrusting it would split this asset:
              </p>
              {manualBridge && manual.data && !manual.data.would_split && (
                <div className={styles.splitWarn}>
                  Distrusting <code>{manualBridge}</code> alone won’t separate this asset — it’s still tied to the rest of the device by its other identifiers.
                </div>
              )}
              <div className={styles.splitKeys}>
                {mergeKeys.map((k) => (
                  <button key={`${k.type}:${k.value}`}
                    className={[styles.splitKeyRow, manualBridge === k.component ? styles.splitKeyActive : ''].filter(Boolean).join(' ')}
                    onClick={() => { setPrimaryIdx(0); setManualBridge(k.component); }}>
                    <span className={styles.splitKeyType}>{k.type}</span>
                    <span className={styles.splitComp}>{k.value}</span>
                  </button>
                ))}
              </div>
            </details>
            </>
          )}
        </>
      )}
    </div>
  );
}
