// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState } from 'react';
import { useIntegrationGroup } from '@/api/integrations';
import { resolveLogoUrl } from '@/lib/logoUrl';
import type { IntegrationGroup } from '@/api/integrations';
import { DataSourceTable, CategoryIcons } from './DataSources';
import styles from './dashboard.module.css';

interface SourceItem {
  name: string;
  integration_id?: string;
  id?: string;
  asset_count?: number;
  assetCount?: number;
  last_sync_at?: string;
  lastSync?: string;
  confidence?: number;
  confidenceDistribution?: { high: number; medium: number; low: number };
  categoryDistribution?: Record<string, number>;
  category?: string;
}

interface DataSourceGroupsProps {
  groups: IntegrationGroup[];
  // Lookup of source records by name (the group-member id matches the
  // dashboard source `name`). Used when a badge is expanded so the
  // member cards render with the same asset count / confidence info as
  // the standalone Data Sources section below.
  sourcesByName: Map<string, SourceItem>;
  // Integration name → group name. Lets each group row roll up its members'
  // asset count and category mix without fetching member details.
  groupMapping: Record<string, string>;
}

export function DataSourceGroups({ groups, sourcesByName, groupMapping }: DataSourceGroupsProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // Lazy: only fetch members for the group the user actually opens.
  const { data: detail } = useIntegrationGroup(expandedId ?? undefined);

  // Per-group rollup of total assets + category mix, summed from the member
  // sources (keyed by group name via the mapping).
  const groupAgg = useMemo(() => {
    const agg: Record<string, { total: number; dist: Record<string, number> }> = {};
    for (const g of groups) agg[g.name] = { total: 0, dist: {} };
    for (const s of sourcesByName.values()) {
      const gname = groupMapping[s.name];
      const bucket = gname ? agg[gname] : undefined;
      if (!bucket) continue;
      bucket.total += s.asset_count || s.assetCount || 0;
      for (const [cat, n] of Object.entries(s.categoryDistribution ?? {})) {
        bucket.dist[cat] = (bucket.dist[cat] || 0) + (n || 0);
      }
    }
    return agg;
  }, [groups, sourcesByName, groupMapping]);

  if (groups.length === 0) return null;

  const members = detail?.group?.members ?? [];
  const memberSources = members
    .map((m) => sourcesByName.get(m.name))
    .filter((s): s is SourceItem => !!s);

  return (
    <div className={styles.groupsBlock}>
      <div className={styles.label}>Data Source Groups</div>
      <div className={styles.groupList}>
        {groups.map((g) => {
          const isOpen = g.id === expandedId;
          return (
            <div key={g.id}>
              <button
                type="button"
                className={`${styles.groupRow} ${isOpen ? styles.groupRowOpen : ''}`}
                onClick={() => setExpandedId(isOpen ? null : g.id)}
                aria-expanded={isOpen}
              >
                {g.logo && (
                  <img src={resolveLogoUrl(g.logo) ?? ''} alt="" className={styles.groupRowLogo} />
                )}
                <span className={styles.groupRowName}>{g.name}</span>
                <span className={styles.srcSep} aria-hidden />
                <span className={styles.srcRowCount}>{(groupAgg[g.name]?.total ?? 0).toLocaleString()}</span>
                <span className={styles.srcSep} aria-hidden />
                <CategoryIcons distribution={groupAgg[g.name]?.dist} />
                <span className={styles.srcSep} aria-hidden />
                <span className={styles.groupRowCount}>
                  {g.member_count}
                  <span className={styles.groupRowCountLabel}> sources</span>
                </span>
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className={`${styles.groupRowChev} ${isOpen ? styles.groupRowChevOpen : ''}`}
                  aria-hidden="true"
                >
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>
              {isOpen && memberSources.length > 0 && (
                <div className={styles.groupMembers}>
                  <DataSourceTable sources={memberSources} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
