// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef } from 'react';
import { tagRulesApi } from '@/api/tag-rules';
import type { TagRuleCondition, ComplianceCriteria } from '@/api/tag-rules';
import { isFilterValueReady } from '@/components/ui/FilterRow';
import styles from '../rules.module.css';

/** A filter is "ready" if its value is complete for its operator (valueless ops
 *  are always ready; between needs both bounds; within_last needs an amount). */
function isFilterReady(f: TagRuleCondition): boolean {
  return isFilterValueReady(f.operator, f.value);
}

interface PreviewCounterProps {
  filters: TagRuleCondition[];
  complianceCriteria?: ComplianceCriteria;
}

export function PreviewCounter({ filters, complianceCriteria }: PreviewCounterProps) {
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState<number | null>(null);
  const [passCount, setPassCount] = useState<number | null>(null);
  const [failCount, setFailCount] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Serialize to detect actual content changes, not reference changes
  const filtersKey = JSON.stringify(filters);
  const criteriaKey = JSON.stringify(complianceCriteria);

  useEffect(() => {
    const hasFilledFilter = filters.some(isFilterReady);
    if (!hasFilledFilter) {
      setTotal(null);
      setPassCount(null);
      setFailCount(null);
      return;
    }

    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }

    timerRef.current = setTimeout(() => {
      setLoading(true);

      // Only send filters that are ready — incomplete rows (operator picked
      // but value still empty) would either no-op the SQL or get rejected by
      // the backend.
      const payload: { target_filters: TagRuleCondition[]; compliance_criteria?: ComplianceCriteria } = {
        target_filters: filters.filter(isFilterReady),
      };
      // Only include compliance_criteria if it has conditions
      if (complianceCriteria && complianceCriteria.conditions.length > 0) {
        payload.compliance_criteria = complianceCriteria;
      }

      tagRulesApi
        .preview(payload)
        .then((result) => {
          setTotal(result.total_matching);
          setPassCount(result.pass_count ?? null);
          setFailCount(result.fail_count ?? null);
        })
        .catch(() => {
          setTotal(null);
          setPassCount(null);
          setFailCount(null);
        })
        .finally(() => {
          setLoading(false);
        });
    }, 500);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersKey, criteriaKey]);

  const hasFilledFilter = filters.some(isFilterReady);
  if (!hasFilledFilter) {
    return null;
  }

  return (
    <div className={styles.previewCounter}>
      <span className={styles.previewNumber}>
        {loading ? '...' : total ?? '--'}
      </span>
      <span className={styles.previewLabel}>assets match</span>
      {!loading && passCount !== null && failCount !== null && (
        <div className={styles.previewCounts}>
          <span className={styles.previewPass}>pass: {passCount}</span>
          <span className={styles.previewFail}>fail: {failCount}</span>
        </div>
      )}
    </div>
  );
}
