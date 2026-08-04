// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import type { Alert } from '@/api/alerts';
import { ALERT_TYPES, ALERT_TYPE_COLORS, ALERT_TYPE_LABELS } from '@/constants/alertTypes';
import { RotatingPie } from '@/components/charts';
import styles from './category-pie.module.css';

interface CategoryPieProps {
  /** Alerts already narrowed by the page's time picker (and source filter),
   *  so the counts always agree with the chart beside them. */
  alerts: Alert[];
  /** Category currently filtering the list (from a slice click), if any. */
  activeCategory?: string | null;
  /** Slice click — the page toggles its list filter with this. */
  onCategoryToggle?: (category: string) => void;
}

/** Alert distribution by rule category (security / compliance / operational /
 *  data quality) as a rotating pie — every slice separates after the entrance
 *  spin — with a legend carrying the exact counts. Clicking a slice filters
 *  the alert list by that category; clicking it again clears. */
export function CategoryPie({ alerts, activeCategory, onCategoryToggle }: CategoryPieProps) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {
      security: 0,
      compliance: 0,
      operational: 0,
      data_quality: 0,
    };
    for (const a of alerts) {
      if (c[a.type] !== undefined) c[a.type]++;
    }
    return c;
  }, [alerts]);

  const slices = ALERT_TYPES.map((type) => ({
    label: ALERT_TYPE_LABELS[type],
    value: counts[type],
    color: ALERT_TYPE_COLORS[type],
    emphasized: true,
  }));

  const labelToType = (label: string): string | undefined =>
    ALERT_TYPES.find((type) => ALERT_TYPE_LABELS[type] === label);

  return (
    <div className={styles.panel}>
      <RotatingPie
        slices={slices}
        size={150}
        selectedLabel={activeCategory ? ALERT_TYPE_LABELS[activeCategory] : null}
        onSliceClick={
          onCategoryToggle
            ? (s) => {
                const type = labelToType(s.label);
                if (type) onCategoryToggle(type);
              }
            : undefined
        }
      />
      <div className={styles.legend}>
        {ALERT_TYPES.map((type) => (
          <div key={type} className={styles.legendRow} data-testid={`category-legend-${type}`}>
            <span className={styles.legendLabel} style={{ color: ALERT_TYPE_COLORS[type] }}>
              {ALERT_TYPE_LABELS[type]}
            </span>
            <span className={styles.legendCount}>{counts[type].toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
