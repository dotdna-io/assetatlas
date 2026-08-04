// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { useBatches } from '@/api/integrations';
import { formatRelativeTime, formatDuration } from '@/pages/integrations/integration-utils';
import styles from './BatchesTable.module.css';

interface BatchesTableProps {
  integrationId: string;
  refetchInterval: number;
}

const PAGE_SIZE = 10;

function getStatusClass(status: string): string {
  if (status === 'success' || status === 'completed') return styles.statusOk;
  if (status === 'failed') return styles.statusError;
  if (status === 'running') return styles.statusInfo;
  return styles.statusMuted;
}

export function BatchesTable({ integrationId, refetchInterval }: BatchesTableProps) {
  const [offset, setOffset] = useState(0);

  const { data, isLoading } = useBatches(
    integrationId,
    { limit: PAGE_SIZE, offset },
    { refetchInterval },
  );

  const batches = data?.batches ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>Recent Batches</div>

      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <colgroup><col style={{ width: '25%' }} /><col style={{ width: '15%' }} /><col style={{ width: '15%' }} /><col style={{ width: '15%' }} /><col style={{ width: '15%' }} /><col style={{ width: '15%' }} /></colgroup>
          <thead>
            <tr>
              <th className={styles.th}>Started</th>
              <th className={styles.th}>Status</th>
              <th className={styles.th}>Records</th>
              <th className={styles.th}>Mapped</th>
              <th className={styles.th}>Assets</th>
              <th className={styles.th}>Duration</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td className={styles.td} colSpan={6}>
                  <div className={styles.emptyState}>Loading…</div>
                </td>
              </tr>
            ) : batches.length === 0 ? (
              <tr>
                <td className={styles.td} colSpan={6}>
                  <div className={styles.emptyState}>No batches yet</div>
                </td>
              </tr>
            ) : (
              batches.map((batch) => (
                <tr key={batch.id} className={styles.tr}>
                  <td className={styles.td}>
                    {new Date(batch.started_at).toLocaleString()} ({formatRelativeTime(batch.started_at)})
                  </td>
                  <td className={styles.td}>
                    <span className={getStatusClass(batch.status)}>{batch.status}</span>
                  </td>
                  <td className={styles.td}>{batch.total_records?.toLocaleString() || '—'}</td>
                  <td className={styles.td}>{batch.records_mapped?.toLocaleString() || '—'}</td>
                  <td className={styles.td}>{batch.assets_created?.toLocaleString() || '—'}</td>
                  <td className={styles.td}>{formatDuration(batch.duration_seconds)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {total > PAGE_SIZE && (
        <div className={styles.pagination}>
          <Button
            variant="secondary"
            size="sm"
            disabled={currentPage === 1}
            onClick={() => setOffset(offset - PAGE_SIZE)}
          >
            Prev
          </Button>
          <span className={styles.pageInfo}>
            Page {currentPage} of {totalPages}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={currentPage === totalPages}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
