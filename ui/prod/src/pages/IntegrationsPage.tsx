// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useDeferredValue, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import {
  useIntegrations,
  useIntegrationGroups,
  useRunIntegration,
  useToggleIntegration,
  useDeleteIntegration,
  useDeleteGroup,
  integrationsApi,
} from '@/api/integrations';
import type { IntegrationGroup } from '@/api/integrations';
import { useDocumentTitle } from '@/hooks';
import { useAdaptivePolling } from '@/hooks/useAdaptivePolling';
import { IntegrationFilters, ViewToggle } from './integrations/IntegrationFilters';
import { IntegrationCard } from './integrations/IntegrationCard';
import { IntegrationTable } from './integrations/IntegrationTable';
import { IntegrationGroups } from './integrations/IntegrationGroups';
import { CreateGroupModal } from './integrations/CreateGroupModal';
import { EditGroupModal } from './integrations/EditGroupModal';
import { AddMembersModal } from './integrations/AddMembersModal';
import styles from './IntegrationsPage.module.css';

export default function IntegrationsPage() {
  useDocumentTitle('Integrations');
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [status, setStatus] = useInlineStatus();

  const [searchQuery, setSearchQuery] = useState('');
  const deferredSearch = useDeferredValue(searchQuery);

  const [viewMode, setViewMode] = useState<'cards' | 'table'>(() => {
    try {
      const stored = localStorage.getItem('integrationViewMode');
      return stored === 'table' ? 'table' : 'cards';
    } catch {
      return 'cards';
    }
  });


  // Modal / dialog state
  const [createGroupOpen, setCreateGroupOpen] = useState(false);
  const [editGroup, setEditGroup] = useState<IntegrationGroup | null>(null);
  const [addMembersGroup, setAddMembersGroup] = useState<IntegrationGroup | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<{
    type: 'integration' | 'group';
    id: string;
    name: string;
  } | null>(null);

  function handleViewModeChange(mode: 'cards' | 'table') {
    setViewMode(mode);
    try {
      localStorage.setItem('integrationViewMode', mode);
    } catch {
      // ignore storage errors
    }
  }

  const { interval, triggerBurst } = useAdaptivePolling();

  const { data: integrationsData } = useIntegrations(undefined, { refetchInterval: interval });
  const { data: groupsData } = useIntegrationGroups({ refetchInterval: interval });

  const allIntegrations = integrationsData?.integrations ?? [];
  const groups = groupsData?.groups ?? [];

  const filtered = useMemo(() => {
    const query = deferredSearch.toLowerCase();
    if (!query) return allIntegrations;
    return allIntegrations.filter((integration) =>
      integration.name.toLowerCase().includes(query) ||
      integration.integration_type.toLowerCase().includes(query) ||
      (integration.description ?? '').toLowerCase().includes(query)
    );
  }, [allIntegrations, deferredSearch]);

  // Mutations
  const runIntegration = useRunIntegration({
    onSuccess: () => {
      triggerBurst();
      setStatus({ kind: 'success', message: 'Integration pipeline started' });
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: err.message || 'Failed to run integration' });
    },
  });

  const toggleIntegration = useToggleIntegration({
    onMutate: async (variables) => {
      await qc.cancelQueries({ queryKey: ['integrations', 'list'] });
      const prev = qc.getQueryData(['integrations', 'list', undefined]);
      qc.setQueriesData<any>({ queryKey: ['integrations', 'list'] }, (old: any) => {
        if (!old?.integrations) return old;
        return {
          ...old,
          integrations: old.integrations.map((i: any) =>
            i.id === variables.id ? { ...i, status: variables.enable ? 'enabled' : 'disabled' } : i
          ),
        };
      });
      return { prev };
    },
    onSuccess: (_data, variables) => {
      triggerBurst();
      setStatus({
        kind: 'success',
        message: variables.enable ? 'Integration enabled' : 'Integration disabled',
      });
    },
    onError: (err, _variables, context: any) => {
      if (context?.prev) {
        qc.setQueriesData({ queryKey: ['integrations', 'list'] }, context.prev);
      }
      setStatus({ kind: 'error', message: err.message || 'Failed to toggle integration' });
    },
  });

  const deleteIntegration = useDeleteIntegration({
    onSuccess: () => {
      triggerBurst();
      setStatus(null); // the row leaves the list — that is the confirmation
      setDeleteConfirm(null);
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: err.message || 'Failed to delete integration' });
    },
  });

  const deleteGroup = useDeleteGroup({
    onSuccess: () => {
      setStatus(null); // the group leaves the list
      setDeleteConfirm(null);
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: err.message || 'Failed to delete group' });
    },
  });

  // Handlers wired to child components
  function handleRun(id: string) {
    runIntegration.mutate(id);
  }

  function handleToggle(id: string, enable: boolean) {
    toggleIntegration.mutate({ id, enable });
  }

  function handleEdit(id: string) {
    navigate(`/integration-wizard?mode=edit&id=${id}`);
  }

  function handleClone(id: string) {
    navigate(`/integration-wizard?mode=clone&id=${id}`);
  }

  function handleDelete(id: string) {
    const integration = allIntegrations.find((i) => i.id === id);
    setDeleteConfirm({ type: 'integration', id, name: integration?.name ?? id });
  }

  function handleConfirmDelete() {
    if (!deleteConfirm) return;
    if (deleteConfirm.type === 'integration') {
      deleteIntegration.mutate(deleteConfirm.id);
    } else {
      deleteGroup.mutate(deleteConfirm.id);
    }
  }

  const removeGroupMemberMutation = useMutation({
    mutationFn: ({ groupId, integrationId }: { groupId: string; integrationId: string }) =>
      integrationsApi.removeGroupMember(groupId, integrationId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integrations', 'groups'] });
      qc.invalidateQueries({ queryKey: ['integrations', 'list'] });
      triggerBurst();
      setStatus(null); // the member leaves the group
    },
    onError: (err: any) => {
      setStatus({ kind: 'error', message: err?.message || 'Failed to remove member' });
    },
  });

  // Group handlers
  function handleRemoveMember(groupId: string, integrationId: string) {
    removeGroupMemberMutation.mutate({ groupId, integrationId });
  }

  function handleDeleteGroup(group: IntegrationGroup) {
    setDeleteConfirm({ type: 'group', id: group.id, name: group.name });
  }

  return (
    <div className={styles.page}>
      <InlineStatus status={status} />
      <IntegrationFilters
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onNewIntegration={() => navigate('/integration-wizard?mode=create')}
      />

      <IntegrationGroups
        groups={groups}
        onCreateGroup={() => setCreateGroupOpen(true)}
        onEditGroup={(group) => setEditGroup(group)}
        onDeleteGroup={handleDeleteGroup}
        onAddMembers={(group) => setAddMembersGroup(group)}
        onRemoveMember={handleRemoveMember}
      />

      <div className={styles.integrationsHeader}>
        <span className={styles.integrationsTitle}>Integrations</span>
        <ViewToggle viewMode={viewMode} onViewModeChange={handleViewModeChange} />
      </div>

      {viewMode === 'cards' ? (
        <div className={styles.grid}>
          {filtered.length === 0 ? (
            <div className={styles.emptyState}>No integrations found</div>
          ) : (
            filtered.map((integration) => (
              <IntegrationCard
                key={integration.id}
                integration={integration}
                onRunStarted={triggerBurst}
                onToggle={handleToggle}
                onEdit={handleEdit}
                onClone={handleClone}
                onDelete={handleDelete}
              />
            ))
          )}
        </div>
      ) : (
        <IntegrationTable
          integrations={filtered}
          onRun={handleRun}
          onToggle={handleToggle}
          onEdit={handleEdit}
          onClone={handleClone}
          onDelete={handleDelete}
        />
      )}

      {/* Modals */}
      <CreateGroupModal
        isOpen={createGroupOpen}
        onClose={() => setCreateGroupOpen(false)}
        integrations={allIntegrations}
      />

      <EditGroupModal
        isOpen={editGroup !== null}
        onClose={() => setEditGroup(null)}
        group={editGroup}
      />

      <AddMembersModal
        isOpen={addMembersGroup !== null}
        onClose={() => setAddMembersGroup(null)}
        group={addMembersGroup}
        integrations={allIntegrations}
      />

      <ConfirmDialog
        isOpen={deleteConfirm !== null}
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteConfirm(null)}
        title={deleteConfirm?.type === 'group' ? 'Delete Group' : 'Delete Integration'}
        message={
          deleteConfirm?.type === 'group'
            ? `Are you sure you want to delete the group "${deleteConfirm.name}"?`
            : `Are you sure you want to delete "${deleteConfirm?.name}"? This action cannot be undone.`
        }
        confirmLabel="Delete"
        variant="danger"
      />
    </div>
  );
}
