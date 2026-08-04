// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { useUpdateGroup } from '@/api/integrations';
import type { IntegrationGroup } from '@/api/integrations';
import styles from './GroupModals.module.css';

interface EditGroupModalProps {
  isOpen: boolean;
  onClose: () => void;
  group: IntegrationGroup | null;
}

export function EditGroupModal({ isOpen, onClose, group }: EditGroupModalProps) {
  const [status, setStatus] = useInlineStatus();
  const [name, setName] = useState('');

  // Pre-fill name when group changes
  useEffect(() => {
    setName(group?.name ?? '');
  }, [group]);

  function handleClose() {
    setName('');
    onClose();
  }

  const updateGroup = useUpdateGroup(group?.id ?? '', {
    onSuccess: () => {
      setStatus(null); // the updated group is on screen
      handleClose();
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: err.message || 'Failed to update group' });
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !group) return;
    updateGroup.mutate({ name: name.trim() });
  }

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="Edit Group" size="sm" showCloseButton={false}>
      <InlineStatus status={status} />
      <form className={styles.form} onSubmit={handleSubmit}>
        <div>
          <label className={styles.label}>Group Name</label>
          <input
            className={styles.input}
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Enter group name"
            required
            autoFocus
          />
        </div>

        <div className={styles.footer}>
          <Button
            variant="secondary"
            type="button"
            onClick={handleClose}
            disabled={updateGroup.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            loading={updateGroup.isPending}
            disabled={!name.trim()}
          >
            Save Changes
          </Button>
        </div>
      </form>
    </Modal>
  );
}
