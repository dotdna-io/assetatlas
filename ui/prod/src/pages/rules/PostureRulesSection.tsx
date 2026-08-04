// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { usePostureCategories } from '@/api/posture-rules';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { PostureCategoryCard } from './posture/PostureCategoryCard';
import { RuleSubsection } from './RuleSubsection';
import styles from './PostureRulesSection.module.css';

interface PostureRulesSectionProps {
  searchQuery: string;
}

/**
 * Posture sub-section of the Rules page (under "AssetAtlas Rules"). Renders the
 * posture category cards — moved here from the former Settings → Posture tab.
 */
export function PostureRulesSection({ searchQuery }: PostureRulesSectionProps) {
  const { data, isLoading, error } = usePostureCategories();

  const q = searchQuery.trim().toLowerCase();
  const categories = useMemo(() => {
    const cats = data?.categories ?? [];
    if (!q) return cats;
    return cats.filter((c) =>
      c.name.toLowerCase().includes(q) ||
      c.description.toLowerCase().includes(q) ||
      c.sub_rules.some((sr) =>
        sr.name.toLowerCase().includes(q) || (sr.description ?? '').toLowerCase().includes(q),
      ),
    );
  }, [data, q]);

  // While searching, hide the whole sub-section if nothing matches.
  if (!isLoading && !error && categories.length === 0) return null;

  return (
    <RuleSubsection
      title="Posture"
      // "categories", not "rules": this group holds posture categories, each
      // of which contains its own sub-rules. Saying "rules" here would mean
      // something different from the alert groups beside it.
      count={
        isLoading || error
          ? undefined
          : `${categories.length} ${categories.length === 1 ? 'category' : 'categories'}`
      }
    >
      {error ? (
        <p className={styles.error}>Failed to load posture rules.</p>
      ) : isLoading ? (
        <LoadingSpinner />
      ) : (
        <div className={styles.cards}>
          {categories.map((cat) => (
            <PostureCategoryCard key={cat.id} category={cat} />
          ))}
        </div>
      )}
    </RuleSubsection>
  );
}
