// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/EnrichmentTab.tsx
import { useState } from 'react';
import { useEnrichmentDatasets } from '@/api/settings';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { DatasetTable } from './enrichment/DatasetTable';
import { UploadModal } from './enrichment/UploadModal';
import styles from './EnrichmentTab.module.css';

export function EnrichmentTab() {
  const { data, isLoading } = useEnrichmentDatasets();
  const [showUpload, setShowUpload] = useState(false);

  if (isLoading) {
    return <LoadingSpinner />;
  }

  const datasets = data?.datasets ?? [];

  // UploadModal is rendered once, in a stable position outside the empty/table
  // branches. If it were duplicated inside each branch, a successful first
  // upload (datasets 0 -> 1, via the query invalidation in
  // useUploadEnrichmentDataset) would flip the branch and remount the modal,
  // discarding its post-upload success/preview view and resetting the form.
  return (
    <>
      {datasets.length === 0 ? (
        <div className={styles.emptyState}>
          <div className={styles.emptyIcon}>
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="12" y1="18" x2="12" y2="12" />
              <line x1="9" y1="15" x2="15" y2="15" />
            </svg>
          </div>
          <h3 className={styles.emptyTitle}>No enrichment datasets configured</h3>
          <p className={styles.emptyDesc}>
            Upload a CSV file to enrich your asset data with custom fields.
          </p>
          <Button onClick={() => setShowUpload(true)}>Upload CSV</Button>
        </div>
      ) : (
        <>
          {/* Two ways a column lands: Apply writes it now, and stage 21
              (apply_all_active) re-applies every active dataset on each
              pipeline run so newly generated assets get enriched too. Saying
              only "next pipeline run" would be wrong — the Apply button sits
              in this very table. Shown on the list rather than the empty
              state: this is where someone lands after uploading and starts
              wondering where the data went. */}
          <p className={styles.pipelineNote}>
            Enrichment columns are added to matching assets on the next pipeline run, or straight
            away when you select <strong>Apply</strong> on a dataset below.
          </p>

          <div className={styles.header}>
            <span className={styles.count}>
              <strong>{datasets.length}</strong> enrichment dataset{datasets.length !== 1 ? 's' : ''}
            </span>
            <Button onClick={() => setShowUpload(true)}>Upload CSV</Button>
          </div>

          <DatasetTable datasets={datasets} />
        </>
      )}

      <UploadModal isOpen={showUpload} onClose={() => setShowUpload(false)} />
    </>
  );
}
