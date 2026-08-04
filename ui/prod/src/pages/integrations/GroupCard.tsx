// SPDX-License-Identifier: AGPL-3.0-or-later
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import { DeleteIconButton, IconButton } from '@/components/ui/IconButton';
import type { IntegrationGroup } from '@/api/integrations';
import { useIntegrationGroup } from '@/api/integrations';
import { resolveLogoUrl } from '@/lib/logoUrl';
import { getConnectorDisplayName } from './integration-utils';
import styles from './GroupCard.module.css';

interface GroupCardProps {
  group: IntegrationGroup;
  expanded: boolean;
  onToggleExpand: () => void;
  onEdit: (group: IntegrationGroup) => void;
  onDelete: (group: IntegrationGroup) => void;
  onAddMembers: (group: IntegrationGroup) => void;
  onRemoveMember: (groupId: string, integrationId: string) => void;
}

export function GroupCard({
  group,
  expanded,
  onToggleExpand,
  onEdit,
  onDelete,
  onAddMembers,
  onRemoveMember,
}: GroupCardProps) {
  // Fetch full group details (with members) only when expanded
  const { data: detailData } = useIntegrationGroup(expanded ? group.id : undefined);
  const members = detailData?.group?.members ?? [];

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div className={styles.headerLeft} onClick={onToggleExpand}>
          <span className={styles.groupName}>{group.name}</span>
        </div>
        <div className={styles.headerActions}>
          <IconButton onClick={() => onAddMembers(group)} title="Add Members">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          </IconButton>
          <IconButton onClick={() => onEdit(group)} title="Rename">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </IconButton>
          <DeleteIconButton onClick={() => onDelete(group)} title="Delete" />
        </div>
      </div>
      <div className={styles.typeRow}>
        <DataSourceBadge name={getConnectorDisplayName(group.integration_type)} />
        <button
          type="button"
          className={styles.expandButton}
          onClick={onToggleExpand}
          aria-label={expanded ? 'Collapse members' : 'Expand members'}
        >
          {group.member_count} {group.member_count === 1 ? 'integration' : 'integrations'}
          <svg
            className={`${styles.chevron} ${expanded ? styles.chevronExpanded : ''}`}
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>

      {expanded && (
        <div className={styles.body}>
          {members.length > 0 ? (
            <div className={styles.memberList}>
              {members.map((member) => (
                <div key={member.id} className={styles.memberRow}>
                  <div className={styles.memberInfo}>
                    {member.logo && (
                      <img src={resolveLogoUrl(member.logo) ?? ''} alt="" className={styles.memberLogo} />
                    )}
                    <span className={styles.memberName}>{member.name}</span>
                  </div>
                  <DeleteIconButton onClick={() => onRemoveMember(group.id, member.id)} title="Remove member" />
                </div>
              ))}
            </div>
          ) : (
            <div className={styles.emptyMembers}>No members in this group</div>
          )}
        </div>
      )}
    </div>
  );
}
