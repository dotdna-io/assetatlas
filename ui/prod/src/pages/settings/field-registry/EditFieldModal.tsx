// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/field-registry/EditFieldModal.tsx
import { useState, useEffect } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useUpdateFieldRegistry } from '@/api/settings';
import type { FieldRegistryEntry } from '@/api/settings';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './EditFieldModal.module.css';

interface EditFieldModalProps {
  isOpen: boolean;
  onClose: () => void;
  field: FieldRegistryEntry | null;
}

export function EditFieldModal({ isOpen, onClose, field }: EditFieldModalProps) {
  const [displayName, setDisplayName] = useState('');
  const [description, setDescription] = useState('');
  const updateField = useUpdateFieldRegistry();
  const [status, setStatus] = useInlineStatus();

  useEffect(() => {
    if (field) {
      setDisplayName(field.display_name || '');
      setDescription(field.description || '');
    }
  }, [field]);

  function handleSave() {
    if (!field) return;
    updateField.mutate(
      { fieldName: field.field_name, data: { display_name: displayName, description } },
      {
        onSuccess: () => { setStatus(null); onClose(); }, // the modal closes onto the updated field
        onError: (err) => setStatus({ kind: 'error', message: err.message || 'Failed to update' }),
      },
    );
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Edit ${field?.field_name ?? ''}`}
      size="md"
      showCloseButton={false}
    >
      <InlineStatus status={status} />
      <div className={styles.field}>
        <label className={styles.label}>Display Name</label>
        <input
          className={styles.input}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.label}>Description</label>
        <textarea
          className={styles.textarea}
          rows={5}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className={styles.footer}>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button onClick={handleSave} loading={updateField.isPending}>Save</Button>
      </div>
    </Modal>
  );
}
