// SPDX-License-Identifier: AGPL-3.0-or-later
import { useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import {
  useReferenceDataStatus,
  useCheckForUpdates,
  useApplyFromUpstream,
  useUploadBundle,
  type ApplyResult,
  type CheckResult,
} from '@/api/referenceData';
import styles from './ReferenceDataTab.module.css';

function formatResultMessage(r: ApplyResult): string {
  if (r.message) return r.message;
  if (r.status === 'failed') return `Failed: ${r.error_message ?? 'unknown error'}`;
  const lines = r.datasets.map(
    (d) => `  • ${d.name}: ${d.rows_before} → ${d.rows_after} rows`,
  );
  return [`Applied bundle ${r.bundle_version} (${r.source})`, ...lines].join('\n');
}

export function ReferenceDataTab() {
  const { data, isLoading } = useReferenceDataStatus();
  const check = useCheckForUpdates();
  const applyUpstream = useApplyFromUpstream();
  const upload = useUploadBundle();
  const fileRef = useRef<HTMLInputElement>(null);

  const [pending, setPending] = useState<CheckResult | null>(null);
  const [resultMessage, setResultMessage] = useState<string>('');

  if (isLoading) return <LoadingSpinner />;

  const status = data ?? {
    current_bundle_version: null,
    last_applied_at: null,
    datasets: [],
    history: [],
  };

  const onCheck = () => {
    setPending(null);
    setResultMessage('');
    check.mutate(undefined, {
      onSuccess: (r) => {
        if (r.status === 'upstream_unavailable') {
          setResultMessage(`Upstream unavailable: ${r.message ?? ''}`);
        } else {
          setPending(r);
        }
      },
      onError: (e) => setResultMessage(`Error: ${e.message}`),
    });
  };

  const applyVersion = (version?: string) => {
    setPending(null);
    setResultMessage('');
    applyUpstream.mutate(
      { version },
      {
        onSuccess: (r) => setResultMessage(formatResultMessage(r)),
        onError: (e) => setResultMessage(`Error: ${e.message}`),
      },
    );
  };

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setPending(null);
    setResultMessage('');
    upload.mutate(f, {
      onSuccess: (r) => setResultMessage(formatResultMessage(r)),
      onError: (err) => setResultMessage(`Error: ${err.message}`),
    });
    e.target.value = '';
  };

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h2>Reference Data</h2>
        <p>
          Update the MAC vendor and OS end-of-life databases. Use{' '}
          <strong>Check for updates</strong> on internet-connected installs, or{' '}
          <strong>Upload bundle</strong> to apply a tarball obtained from a
          connected machine on air-gapped installs.
        </p>
      </div>

      <div className={styles.statusCard}>
        <div className={styles.statusRow}>
          <span className={styles.label}>Current bundle:</span>
          <span>{status.current_bundle_version ?? 'none applied'}</span>
        </div>
        <div className={styles.statusRow}>
          <span className={styles.label}>Last applied:</span>
          <span>{status.last_applied_at ?? '—'}</span>
        </div>
      </div>

      <div className={styles.actions}>
        <Button onClick={onCheck} loading={check.isPending}>
          Check for updates
        </Button>
        <Button
          variant="secondary"
          onClick={() => fileRef.current?.click()}
          loading={upload.isPending}
        >
          Upload bundle
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".tar.gz,.tgz"
          style={{ display: 'none' }}
          onChange={onFile}
        />
      </div>

      {pending && pending.available && (
        <div className={styles.confirmPanel}>
          <h3>Update available</h3>
          <p>
            Upstream version <strong>{pending.upstream_version}</strong>
            {pending.current_version && (
              <> &nbsp;(currently <strong>{pending.current_version}</strong>)</>
            )}
            {pending.generated_at && (
              <> &nbsp;— generated {new Date(pending.generated_at).toLocaleString()}</>
            )}
          </p>
          {pending.datasets && (
            <ul className={styles.datasetList}>
              {pending.datasets.map((d) => (
                <li key={d.name}>
                  <strong>{d.name}</strong>: {d.row_count.toLocaleString()} rows from {d.source}
                </li>
              ))}
            </ul>
          )}
          <div className={styles.actions}>
            <Button
              onClick={() => applyVersion(pending.upstream_version)}
              loading={applyUpstream.isPending}
            >
              Apply update
            </Button>
            <Button variant="secondary" onClick={() => setPending(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {pending && !pending.available && (
        <p className={styles.muted}>
          {pending.reason === 'invalid'
            ? `Bundle rejected: ${pending.message}`
            : `Already at latest (${pending.current_version ?? pending.upstream_version}).`}
        </p>
      )}

      {resultMessage && <pre className={styles.result}>{resultMessage}</pre>}

      <h3>Datasets</h3>
      {status.datasets.length === 0 ? (
        <p className={styles.muted}>No bundle has been applied yet.</p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Row count</th>
            </tr>
          </thead>
          <tbody>
            {status.datasets.map((d) => (
              <tr key={d.name}>
                <td>{d.name}</td>
                <td>{d.rows_after}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Recent applies</h3>
      {status.history.length === 0 ? (
        <p className={styles.muted}>No history yet.</p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Started</th>
              <th>Version</th>
              <th>Source</th>
              <th>Status</th>
              <th>By</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {status.history.map((h) => {
              const canRollback =
                h.status === 'success' &&
                h.source === 'github' &&
                h.bundle_version !== status.current_bundle_version;
              return (
                <tr key={h.id}>
                  <td>{new Date(h.started_at).toLocaleString()}</td>
                  <td>{h.bundle_version}</td>
                  <td>{h.source}</td>
                  <td className={h.status === 'failed' ? styles.statusFailed : ''}>
                    {h.status}
                  </td>
                  <td>{h.initiated_by ?? '—'}</td>
                  <td>
                    {canRollback && (
                      <button
                        type="button"
                        className={styles.linkButton}
                        onClick={() => applyVersion(h.bundle_version)}
                        disabled={applyUpstream.isPending}
                      >
                        Re-apply this version
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
