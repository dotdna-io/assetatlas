// SPDX-License-Identifier: AGPL-3.0-or-later
import { CategoryIcon, categoryKey, getCategoryColor } from '@/components/ui/CategoryIcon';
import styles from './CategoryBars.module.css';

export interface CategoryBarsDatum {
  category: string;
  count: number;
}

interface CategoryBarsProps {
  data: CategoryBarsDatum[];
}

/**
 * Asset distribution by category as stacked linear gauges — one category per
 * row (largest first): the category icon, a thick bar in the category colour
 * whose length is proportional to the largest category (no background track),
 * and the asset count placed right after the bar's end.
 */
// Longest bar fills this fraction of the bar zone, leaving room for the count
// to sit right after even the largest bar.
const MAX_BAR_PCT = 66;

export function CategoryBars({ data }: CategoryBarsProps) {
  const rows = data.filter((d) => d.count > 0);
  if (rows.length === 0) return null;
  const max = Math.max(...rows.map((d) => d.count));

  return (
    <div className={styles.bars}>
      {rows.map((d) => {
        const color = getCategoryColor(d.category);
        const pct = max > 0 ? (d.count / max) * MAX_BAR_PCT : 0;
        return (
          <div key={d.category} className={styles.row} title={d.category}>
            <span className={styles.icon} style={{ color }}>
              <CategoryIcon category={categoryKey(d.category)} size={18} />
            </span>
            <div className={styles.barZone}>
              <div className={styles.bar} style={{ width: `${pct}%`, background: color }} />
              <span className={styles.count}>{d.count.toLocaleString()}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
