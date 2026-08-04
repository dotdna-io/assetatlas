// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { useDocumentTitle } from '@/hooks';
import { useDashboardSummary, useDashboardAlerts, useDataSources } from '@/api/dashboard';
import { useIntegrationGroups, useIntegrationGroupMapping, useIntegrations } from '@/api/integrations';
import { FirstRunPanel } from './dashboard/FirstRunPanel';
import { HeroBar, HeroBarSkeleton } from './dashboard/HeroBar';
import { CategoryStrip, CategoryStripSkeleton } from './dashboard/CategoryStrip';
import { ConfidenceFlow } from './dashboard/ConfidenceFlow';
import { ActivityFeed, ActivityFeedSkeleton } from './dashboard/ActivityFeed';
import { TrendCard } from './dashboard/TrendCard';
import { DataSources, DataSourcesSkeleton } from './dashboard/DataSources';
import { DataSourceGroups } from './dashboard/DataSourceGroups';
import pageStyles from './DashboardPage.module.css';
import dashStyles from './dashboard/dashboard.module.css';

export default function DashboardPage() {
  useDocumentTitle('Dashboard');
  const summaryQuery = useDashboardSummary();
  const alertsQuery = useDashboardAlerts();
  const sourcesQuery = useDataSources();
  // Integration grouping — when the user has organised data sources into
  // groups, render the groups (expandable) above the standalone sources.
  // If no groups exist these queries are cheap (empty mapping) and the
  // groups block doesn't render.
  const groupsQuery = useIntegrationGroups();
  const groupMappingQuery = useIntegrationGroupMapping();
  // First run: no integration configured at all. exclude_manual defaults to
  // true on the API, which would keep the panel up for someone who onboarded
  // via a file upload — pass false so any configured integration counts.
  const integrationsQuery = useIntegrations({ exclude_manual: false });

  const summaryLoading = summaryQuery.isPending;
  const alertsLoading = alertsQuery.isPending;
  // Wait for groups + mapping to settle alongside sources so the data
  // sources area renders in its final shape from the first frame.
  // Otherwise sources would briefly appear in the standalone section
  // before the mapping pushes them into a group.
  const sourcesLoading =
    sourcesQuery.isPending ||
    groupsQuery.isPending ||
    groupMappingQuery.isPending;

  const summary = summaryQuery.data;

  // Normalize categoryBreakdown — the API returns a Record (or array). Empty
  // until the summary query has real data; loading and unavailable both fall
  // through to the skeleton below rather than showing fabricated numbers.
  const categoryBreakdown = !summary
    ? []
    : Array.isArray(summary.categoryBreakdown)
      ? summary.categoryBreakdown
      : Object.entries(summary.categoryBreakdown || {}).map(([category, count]) => ({
          category,
          count: count as number,
        }));

  const alertsData = alertsQuery.data ?? { alerts: [] };
  const sourcesData = sourcesQuery.data ?? { sources: [] };

  const allSources: any[] = (sourcesData as any).sources || [];
  const groups = groupsQuery.data?.groups ?? [];
  const groupMapping = groupMappingQuery.data?.mapping ?? {};

  // Split sources into grouped (in some group) and ungrouped. The mapping
  // endpoint keys on integration name, which matches `source.name`.
  const { sourcesByName, ungroupedSources } = useMemo(() => {
    const byName = new Map<string, any>();
    for (const s of allSources) {
      if (s?.name) byName.set(s.name, s);
    }
    const ungrouped = allSources.filter((s) => !groupMapping[s?.name ?? '']);
    return { sourcesByName: byName, ungroupedSources: ungrouped };
  }, [allSources, groupMapping]);

  // Gate on isSuccess, not on a falsy count: a failed request leaves `data`
  // undefined, and treating that as zero would show first-run guidance to
  // someone who already has integrations.
  if (integrationsQuery.isSuccess && (integrationsQuery.data?.count ?? 0) === 0) {
    return (
      <div className={`${pageStyles.page} ${dashStyles.homepageGlass}`}>
        <FirstRunPanel />
      </div>
    );
  }

  return (
    <div className={`${pageStyles.page} ${dashStyles.homepageGlass}`}>
      {/* Row 1: Hero (total + visibility) with the category strip on the right */}
      <div className={dashStyles.heroRow}>
        {summaryLoading || !summary ? (
          <>
            <HeroBarSkeleton />
            <CategoryStripSkeleton />
          </>
        ) : (
          <>
            <HeroBar totalAssets={summary.totalAssets} lastUpdated={summary.lastUpdated} />
            <CategoryStrip categories={categoryBreakdown} />
          </>
        )}
      </div>

      {/* Row 3: Confidence Chevron Flow (EXPERIMENT — was PostureFlow) */}
      <ConfidenceFlow />

      {/* Row 4: Alerts (width-matched to the Secured chevron) + Added & removed */}
      <div className={dashStyles.gridAlertsTimeline}>
        {alertsLoading ? (
          <ActivityFeedSkeleton />
        ) : (
          <ActivityFeed alerts={(alertsData.alerts || []) as any} />
        )}
        <TrendCard />
      </div>

      {/* Row 5: Data Sources (groups + standalone) — one card for the section */}
      <div className={dashStyles.sourcesCard}>
        {sourcesLoading ? (
          <DataSourcesSkeleton />
        ) : (
          <>
            {groups.length > 0 && (
              <DataSourceGroups groups={groups} sourcesByName={sourcesByName} groupMapping={groupMapping} />
            )}
            <DataSources sources={ungroupedSources} />
          </>
        )}
      </div>
    </div>
  );
}
