// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/enrichment/DatasetTable.tsx
import { useState } from 'react';
import {
  useApplyEnrichmentDataset,
  useDeleteEnrichmentDataset,
  useToggleEnrichmentDataset,
} from '@/api/settings';
import type { EnrichmentDataset } from '@/api/settings';
import { coerceFieldId } from '@/types/field-id';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './DatasetTable.module.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CORE_MATCH_FIELDS: Record<string, string> = {
  hostname: 'Hostname',
  fqdn: 'FQDN',
  ip: 'IP Address',
  mac: 'MAC Address',
  sn: 'Serial Number',
  manufacturer: 'Manufacturer',
  model: 'Model',
};

function matchFieldLabel(field: string): string {
  // Legacy formats (datasets created before canonical encoding).
  if (CORE_MATCH_FIELDS[field]) return CORE_MATCH_FIELDS[field];
  if (field.startsWith('ext.')) {
    const parts = field.split('.');
    if (parts.length === 3) return parts[1] + ' \u2192 ' + parts[2];
  }
  // Canonical encoded field id (e.g. "static:Hostname", "group:Entra:building").
  const fid = coerceFieldId(field);
  if (fid) {
    switch (fid.kind) {
      case 'extended_attribute':
        return fid.source + ' \u2192 ' + fid.attribute;
      case 'integration_group':
        return fid.group + ' \u2192 ' + fid.attribute;
      case 'static':
      case 'core_enrichment':
      case 'tag':
      case 'virtual':
        return fid.name;
      case 'posture':
        return fid.category;
    }
  }
  return field;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface DatasetTableProps {
  datasets: EnrichmentDataset[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DatasetTable({ datasets }: DatasetTableProps) {
  const [status, setStatus] = useInlineStatus();

  const toggleDataset = useToggleEnrichmentDataset();
  const applyDataset = useApplyEnrichmentDataset();
  const deleteDataset = useDeleteEnrichmentDataset();

  const [confirmDelete, setConfirmDelete] = useState<EnrichmentDataset | null>(null);

  function handleToggle(ds: EnrichmentDataset) {
    const newStatus = ds.status === 'active' ? 'inactive' : 'active';
    toggleDataset.mutate(
      { id: ds.id, status: newStatus },
      {
        onSuccess: () =>
          setStatus(null), // the row's toggle shows the new state
        onError: (err) => setStatus({ kind: 'error', message: err.message || 'Failed to update status' }),
      },
    );
  }

  function handleApply(ds: EnrichmentDataset) {
    applyDataset.mutate(ds.id, {
      onSuccess: (result) => {
        const matched = result?.matched_count ?? result?.data?.matched_count ?? 0;
        setStatus({
          kind: 'success',
          message: `Applied: ${matched} asset${matched !== 1 ? 's' : ''} matched`,
          action: `apply:${ds.id}`,
        });
      },
      onError: (err) => setStatus({ kind: 'error', message: err.message || 'Apply failed' }),
    });
  }

  function handleDeleteConfirm() {
    if (!confirmDelete) return;
    deleteDataset.mutate(confirmDelete.id, {
      onSuccess: () => {
        setStatus(null); // the row leaves the table
        setConfirmDelete(null);
      },
      onError: (err) => {
        setStatus({ kind: 'error', message: err.message || 'Failed to delete dataset' });
        setConfirmDelete(null);
      },
    });
  }

  return (
    <>
      <InlineStatus status={status} />
      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr className={styles.headerRow}>
              <th>Name</th>
              <th>Match Field</th>
              <th>Type</th>
              <th>Target Field</th>
              <th className={styles.centerCol}>Rows</th>
              <th className={styles.centerCol}>Matched</th>
              <th>Uploaded</th>
              <th className={styles.centerCol}>Status</th>
              <th className={styles.centerCol}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((ds) => {
              const uploadDate = ds.created_at
                ? new Date(ds.created_at).toLocaleDateString()
                : '-';
              const matchedDisplay =
                ds.matched_count != null
                  ? ds.matched_count.toLocaleString()
                  : '\u2014';

              return (
                <tr key={ds.id} className={styles.dataRow}>
                  <td className={styles.nameCell}>{ds.name}</td>
                  <td>{matchFieldLabel(ds.match_field)}</td>
                  <td>
                    <Badge color={ds.match_type === 'cidr' ? '#8B5CF6' : '#3B82F6'}>
                      {ds.match_type}
                    </Badge>
                  </td>
                  <td className={styles.monoCell}>{ds.target_field}</td>
                  <td className={styles.centerCol}>{ds.row_count.toLocaleString()}</td>
                  <td className={styles.centerCol}>{matchedDisplay}</td>
                  <td className={styles.dateCell}>{uploadDate}</td>
                  <td className={styles.centerCol}>
                    <ToggleSwitch
                      checked={ds.status === 'active'}
                      onChange={() => handleToggle(ds)}
                    />
                  </td>
                  <td className={styles.actionsCell}>
                    <Button
                      variant="success"
                      size="sm"
                      loading={applyDataset.isPending && applyDataset.variables === ds.id}
                      onClick={() => handleApply(ds)}
                      success={successFor(status, `apply:${ds.id}`)}
                    >
                      Apply
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => setConfirmDelete(ds)}
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        isOpen={confirmDelete !== null}
        title="Delete enrichment dataset"
        message={`Delete enrichment dataset '${confirmDelete?.name}'? This cannot be undone.`}
        confirmLabel="Delete"
        variant="danger"
        onConfirm={handleDeleteConfirm}
        onCancel={() => setConfirmDelete(null)}
      />
    </>
  );
}
