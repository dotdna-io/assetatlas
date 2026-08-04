// SPDX-License-Identifier: AGPL-3.0-or-later
import { useAlertRules } from '@/api/alerts';
import type { AlertRule } from '@/api/alerts';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { Button } from '@/components/ui/Button';
import { AlertCard } from '../alerts/AlertCard';
import { RuleSubsection } from './RuleSubsection';

const TYPE_ORDER: AlertRule['type'][] = ['security', 'compliance', 'operational', 'data_quality'];

const TYPE_LABEL: Record<AlertRule['type'], string> = {
  security: 'Security',
  compliance: 'Compliance',
  operational: 'Operational',
  data_quality: 'Data Quality',
};

interface AlertRulesSectionProps {
  searchQuery: string;
  showDisabled: boolean;
  severityFilter?: string;
  typeFilter?: string;
}

export function AlertRulesSection({ searchQuery, showDisabled, severityFilter, typeFilter }: AlertRulesSectionProps) {
  const { data, isLoading, error, refetch } = useAlertRules();

  if (isLoading) {
    return <LoadingSpinner />;
  }

  if (error) {
    return (
      <div>
        <p style={{ color: 'var(--error)', fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>
          Failed to load alert rules.
        </p>
        <Button variant="secondary" size="sm" onClick={() => refetch()}>Retry</Button>
      </div>
    );
  }

  const q = searchQuery.toLowerCase();
  const rules = (data?.rules ?? []).filter((r) => {
    if (!showDisabled && !r.enabled) return false;
    if (q && !r.name.toLowerCase().includes(q) && !r.description.toLowerCase().includes(q)) return false;
    if (severityFilter && r.severity !== severityFilter) return false;
    if (typeFilter && r.type !== typeFilter) return false;
    return true;
  });

  if (rules.length === 0) {
    return (
      <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-size-sm)' }}>
        No alert rules found
      </p>
    );
  }

  // Group by type; within each group, system rules first
  const grouped = TYPE_ORDER
    .map((type) => ({
      type,
      rules: rules
        .filter((r) => r.type === type)
        .sort((a, b) => {
          const rank = (s: string) => s === 'user' ? 1 : 0;
          return rank(a.source) - rank(b.source);
        }),
    }))
    .filter((g) => g.rules.length > 0);

  return (
    <div>
      {grouped.map(({ type, rules: groupRules }) => (
        <RuleSubsection
          key={type}
          title={TYPE_LABEL[type]}
          // groupRules is already search- and disabled-filtered, so the count
          // matches the rows on screen rather than the raw group size.
          count={`${groupRules.length} ${groupRules.length === 1 ? 'rule' : 'rules'}`}
          defaultExpanded={type === 'security'}
        >
          {groupRules.map((rule) => (
            <AlertCard key={rule.id} rule={rule} />
          ))}
        </RuleSubsection>
      ))}
    </div>
  );
}
