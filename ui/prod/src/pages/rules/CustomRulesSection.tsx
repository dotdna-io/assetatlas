// SPDX-License-Identifier: AGPL-3.0-or-later
import { useTagRules } from '@/api/tag-rules';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { Button } from '@/components/ui/Button';
import { RuleCard } from './RuleCard';

interface CustomRulesSectionProps {
  searchQuery: string;
  showDisabled: boolean;
  typeFilter?: string;
}

export function CustomRulesSection({ searchQuery, showDisabled, typeFilter }: CustomRulesSectionProps) {
  const { data, isLoading, error, refetch } = useTagRules();

  if (isLoading) {
    return <LoadingSpinner />;
  }

  if (error) {
    return (
      <div>
        <p style={{ color: 'var(--error)', fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>
          Failed to load rules.
        </p>
        <Button variant="secondary" size="sm" onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const rules = data?.rules ?? [];
  const query = searchQuery.trim().toLowerCase();

  const filtered = rules.filter((rule) => {
    if (!showDisabled && !rule.enabled) return false;
    if (query) {
      const matchesName = rule.name.toLowerCase().includes(query);
      const matchesDesc = rule.description.toLowerCase().includes(query);
      if (!matchesName && !matchesDesc) return false;
    }
    if (typeFilter && rule.type !== typeFilter) return false;
    return true;
  });

  if (filtered.length === 0) {
    return (
      <p style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-size-sm)' }}>
        {rules.length === 0
          ? "No rules defined yet. Click 'Create New Rule' to get started."
          : 'No rules match your filters.'}
      </p>
    );
  }

  return (
    <div>
      {filtered.map((rule) => (
        <RuleCard key={rule.id} rule={rule} />
      ))}
    </div>
  );
}
