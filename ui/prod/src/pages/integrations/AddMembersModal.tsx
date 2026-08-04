// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { useAddGroupMembers, useIntegrationGroup } from '@/api/integrations';
import type { IntegrationGroup, Integration } from '@/api/integrations';
import styles from './GroupModals.module.css';

interface AddMembersModalProps {
  isOpen: boolean;
  onClose: () => void;
  group: IntegrationGroup | null;
  integrations: Integration[];
}

export function AddMembersModal({ isOpen, onClose, group, integrations }: AddMembersModalProps) {
  const [status, setStatus] = useInlineStatus();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Fetch full group detail (with members) when modal opens — uses React Query cache
  const { data: groupDetail, isLoading: loadingMembers } = useIntegrationGroup(
    isOpen && group ? group.id : undefined,
  );
  const members = groupDetail?.group?.members ?? [];

  // Integrations that match the group type and are not already members
  const candidates = useMemo(() => {
    if (!group) return [];
    const existingMemberIds = new Set(members.map((m) => m.id));
    return integrations.filter(
      (i) => i.integration_type === group.integration_type && !existingMemberIds.has(i.id),
    );
  }, [group, integrations, members]);

  function handleClose() {
    setSelectedIds(new Set());
    onClose();
  }

  function toggleId(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  const addMembers = useAddGroupMembers(group?.id ?? '', {
    onSuccess: () => {
      setStatus(null); // the members appear in the group
      handleClose();
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: err.message || 'Failed to add members' });
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (selectedIds.size === 0 || !group) return;
    addMembers.mutate(Array.from(selectedIds));
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={`Add Members to "${group?.name ?? ''}"`}
      size="md"
      showCloseButton={false}
    >
      <InlineStatus status={status} />
      <form className={styles.form} onSubmit={handleSubmit}>
        {loadingMembers ? (
          <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-sm)' }}>Loading members...</p>
        ) : candidates.length === 0 ? (
          <p>No available integrations to add for this group type.</p>
        ) : (
          <div>
            <label className={styles.label}>Select Integrations</label>
            <div className={styles.checkboxList}>
              {candidates.map((integration) => (
                <label key={integration.id} className={styles.checkboxRow}>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(integration.id)}
                    onChange={() => toggleId(integration.id)}
                  />
                  {integration.name}
                </label>
              ))}
            </div>
          </div>
        )}

        <div className={styles.footer}>
          <Button
            variant="secondary"
            type="button"
            onClick={handleClose}
            disabled={addMembers.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            loading={addMembers.isPending}
            disabled={selectedIds.size === 0}
          >
            Add Members
          </Button>
        </div>
      </form>
    </Modal>
  );
}
