// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import type { IntegrationGroup } from '@/api/integrations';
import { GroupCard } from './GroupCard';
import styles from './IntegrationGroups.module.css';

interface IntegrationGroupsProps {
  groups: IntegrationGroup[];
  onCreateGroup: () => void;
  onEditGroup: (group: IntegrationGroup) => void;
  onDeleteGroup: (group: IntegrationGroup) => void;
  onAddMembers: (group: IntegrationGroup) => void;
  onRemoveMember: (groupId: string, integrationId: string) => void;
}

export function IntegrationGroups({
  groups,
  onCreateGroup,
  onEditGroup,
  onDeleteGroup,
  onAddMembers,
  onRemoveMember,
}: IntegrationGroupsProps) {
  const [expandedGroupIds, setExpandedGroupIds] = useState<Set<string>>(new Set());

  function toggleExpand(groupId: string) {
    setExpandedGroupIds((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  }

  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>
        <span className={styles.sectionTitle}>Integration Groups</span>
        <Button variant="secondary" size="sm" onClick={onCreateGroup}>
          Create Group
        </Button>
      </div>

      <div className={styles.groupGrid}>
        {groups.map((group) => (
          <GroupCard
            key={group.id}
            group={group}
            expanded={expandedGroupIds.has(group.id)}
            onToggleExpand={() => toggleExpand(group.id)}
            onEdit={onEditGroup}
            onDelete={onDeleteGroup}
            onAddMembers={onAddMembers}
            onRemoveMember={onRemoveMember}
          />
        ))}
      </div>
    </div>
  );
}
