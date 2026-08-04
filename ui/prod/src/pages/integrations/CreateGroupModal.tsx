// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { useCreateGroup, integrationsApi } from '@/api/integrations';
import { resolveLogoUrl } from '@/lib/logoUrl';
import { getConnectorDisplayName } from './integration-utils';
import type { Integration } from '@/api/integrations';
import styles from './GroupModals.module.css';

interface CreateGroupModalProps {
  isOpen: boolean;
  onClose: () => void;
  integrations: Integration[];
}

export function CreateGroupModal({ isOpen, onClose, integrations }: CreateGroupModalProps) {
  const [status, setStatus] = useInlineStatus();
  const qc = useQueryClient();

  const [name, setName] = useState('');
  const [selectedType, setSelectedType] = useState('');
  const [selectedMemberIds, setSelectedMemberIds] = useState<Set<string>>(new Set());

  const integrationTypes = useMemo(() => {
    const total = new Map<string, number>();
    const available = new Map<string, number>();
    integrations.forEach(i => {
      total.set(i.integration_type, (total.get(i.integration_type) || 0) + 1);
      if (!i.group_id) available.set(i.integration_type, (available.get(i.integration_type) || 0) + 1);
    });
    return [...total.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([type, count]) => ({ type, count, available: available.get(type) || 0 }));
  }, [integrations]);

  const typeMembers = useMemo(() => {
    if (!selectedType) return [];
    return integrations.filter(i => i.integration_type === selectedType);
  }, [integrations, selectedType]);

  useEffect(() => { setSelectedMemberIds(new Set()); }, [selectedType]);

  function handleClose() {
    setName('');
    setSelectedType('');
    setSelectedMemberIds(new Set());
    onClose();
  }

  function toggleMember(id: string) {
    setSelectedMemberIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const createGroup = useCreateGroup({
    onSuccess: async (data) => {
      const memberIds = Array.from(selectedMemberIds);
      if (memberIds.length > 0) {
        try {
          await integrationsApi.addGroupMembers(data.group.id, memberIds);
          qc.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
        } catch (err: any) {
          const detail = err?.body?.message || err?.message || 'Failed to add members';
          setStatus({ kind: 'error', message: `Group created but: ${detail}` });
          handleClose();
          return;
        }
      }
      setStatus(null); // the group appears in the list behind the modal
      handleClose();
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: err.message || 'Failed to create group' });
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !selectedType) return;
    createGroup.mutate({ name: name.trim(), integration_type: selectedType });
  }

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="Create Integration Group" size="md">
      <InlineStatus status={status} />
      <form className={styles.form} onSubmit={handleSubmit}>
        {/* Group Name */}
        <div className={styles.field}>
          <label className={styles.label}>Group Name</label>
          <input
            className={styles.input}
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g. Azure Cloud Sources"
            required
            autoFocus
          />
        </div>

        {/* Integration Type — card selector */}
        <div className={styles.field}>
          <label className={styles.label}>Integration Type</label>
          <div className={styles.typeGrid}>
            {integrationTypes.map(({ type, count, available }) => {
              const disabled = available < 2;
              return (
              <button
                key={type}
                type="button"
                className={`${styles.typeCard} ${selectedType === type ? styles.typeCardActive : ''} ${disabled ? styles.typeCardDisabled : ''}`}
                onClick={() => !disabled && setSelectedType(type)}
                disabled={disabled}
                title={disabled ? `Need at least 2 ungrouped integrations (${available} available)` : undefined}
              >
                <span className={styles.typeCardName}>{getConnectorDisplayName(type)}</span>
                <span className={styles.typeCardCount}>{available} of {count} available</span>
              </button>
              );
            })}
          </div>
        </div>

        {/* Members — toggle cards */}
        {typeMembers.length > 0 && (
          <div className={styles.field}>
            <label className={styles.label}>
              Add Members
              <span className={styles.labelHint}>(optional)</span>
            </label>
            <div className={styles.memberList}>
              {typeMembers.map(integration => {
                const isSelected = selectedMemberIds.has(integration.id);
                const inGroup = !!integration.group_id;
                return (
                  <button
                    key={integration.id}
                    type="button"
                    className={`${styles.memberCard} ${isSelected ? styles.memberCardActive : ''} ${inGroup ? styles.memberCardDisabled : ''}`}
                    onClick={() => !inGroup && toggleMember(integration.id)}
                    disabled={inGroup}
                    title={inGroup ? 'Already in a group' : undefined}
                  >
                    {integration.logo && (
                      <img
                        src={resolveLogoUrl(integration.logo) ?? ''}
                        alt=""
                        className={styles.memberLogo}
                        onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}
                      />
                    )}
                    <span className={styles.memberName}>{integration.name}</span>
                    {isSelected && (
                      <svg className={styles.memberCheck} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                    )}
                    {inGroup && <Badge variant="default">In group</Badge>}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className={styles.footer}>
          <Button variant="ghost" type="button" onClick={handleClose} disabled={createGroup.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            loading={createGroup.isPending}
            disabled={!name.trim() || !selectedType}
          >
            Create Group
          </Button>
        </div>
      </form>
    </Modal>
  );
}
