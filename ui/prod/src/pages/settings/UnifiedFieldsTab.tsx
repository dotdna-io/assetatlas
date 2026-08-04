// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/UnifiedFieldsTab.tsx
import { useState } from 'react';
import { useUnifiedFields, useDeleteUnifiedField } from '@/api/unifiedFields';
import type { UnifiedField } from '@/api/unifiedFields';
import { functionsForType } from './unified-fields/unified-fields-utils';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { UnifiedFieldModal } from './unified-fields/UnifiedFieldModal';
import styles from './UnifiedFieldsTab.module.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function functionLabel(fn: UnifiedField['function'], dataType: string): string {
  const options = functionsForType(dataType);
  return options.find((o) => o.value === fn)?.label ?? fn;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function UnifiedFieldsTab() {
  const { data, isLoading } = useUnifiedFields();
  const deleteField = useDeleteUnifiedField();

  const [modalField, setModalField] = useState<UnifiedField | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<UnifiedField | null>(null);
  const [deleteError, setDeleteError] = useState<string>('');

  if (isLoading) {
    return <LoadingSpinner />;
  }

  const fields = data?.fields ?? [];

  function openAdd() {
    setModalField(undefined);
    setModalOpen(true);
  }

  function openEdit(field: UnifiedField) {
    setModalField(field);
    setModalOpen(true);
  }

  function handleDeleteConfirm() {
    if (!confirmDelete) return;
    const name = confirmDelete.name;
    deleteField.mutate(confirmDelete.id, {
      onSuccess: () => {
        setDeleteError('');
        setConfirmDelete(null);
      },
      onError: () => {
        setDeleteError(`Failed to delete "${name}"`);
        setConfirmDelete(null);
      },
    });
  }

  return (
    <>
      {fields.length === 0 ? (
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
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <line x1="3" y1="9" x2="21" y2="9" />
              <line x1="3" y1="15" x2="21" y2="15" />
              <line x1="9" y1="9" x2="9" y2="21" />
            </svg>
          </div>
          <h3 className={styles.emptyTitle}>No unified fields configured</h3>
          <p className={styles.emptyDesc}>
            Create a unified field to merge values from multiple sources into a single asset field.
          </p>
          <Button onClick={openAdd}>Add unified field</Button>
        </div>
      ) : (
        <>
          <div className={styles.header}>
            <span className={styles.count}>
              <strong>{fields.length}</strong> unified field{fields.length !== 1 ? 's' : ''}
            </span>
            <Button onClick={openAdd}>Add unified field</Button>
          </div>

          <div className={styles.tableWrapper}>
            <table className={styles.table}>
              <thead>
                <tr className={styles.headerRow}>
                  <th>Name</th>
                  <th>Function</th>
                  <th>Sources</th>
                  <th>Data Type</th>
                  <th className={styles.centerCol}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {fields.map((field) => (
                  <tr key={field.id} className={styles.dataRow}>
                    <td className={styles.nameCell}>{field.name}</td>
                    <td>{functionLabel(field.function, field.data_type)}</td>
                    <td>{field.sources.length} field{field.sources.length !== 1 ? 's' : ''}</td>
                    <td>{field.data_type}</td>
                    <td className={styles.actionsCell}>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => openEdit(field)}
                        aria-label={`Edit ${field.name}`}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => setConfirmDelete(field)}
                        aria-label={`Delete ${field.name}`}
                      >
                        Delete
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {deleteError && (
        <p className={styles.deleteError}>{deleteError}</p>
      )}

      {modalOpen && (
        <UnifiedFieldModal
          initial={modalField}
          onClose={() => setModalOpen(false)}
        />
      )}

      <ConfirmDialog
        isOpen={confirmDelete !== null}
        title="Delete unified field"
        message={`Delete unified field '${confirmDelete?.name}'? This cannot be undone.`}
        confirmLabel="Delete"
        variant="danger"
        onConfirm={handleDeleteConfirm}
        onCancel={() => setConfirmDelete(null)}
      />
    </>
  );
}
