// SPDX-License-Identifier: AGPL-3.0-or-later
import { Fragment } from 'react';
import { assetUrl } from '@/basePath';
import { CategoryIcon, CATEGORY_COLORS } from '@/components/ui/CategoryIcon';
import styles from './dashboard.module.css';

interface CategoryEntry {
  category: string;
  count: number;
}

interface CategoryStripProps {
  categories: CategoryEntry[];
}

const CAT_DEFS = [
  { key: 'server', label: 'Server' },
  { key: 'workstation', label: 'Workstation' },
  { key: 'mobile', label: 'Mobile' },
  { key: 'network', label: 'Network' },
  { key: 'iot', label: 'IoT' },
  { key: 'ot', label: 'OT' },
  { key: 'miscellaneous', label: 'Misc' },
];

const SKELETON_PILL_COUNT = 7;

export function CategoryStrip({ categories }: CategoryStripProps) {
  const items = CAT_DEFS
    .map((def) => {
      const match = categories.find((c) => c.category === def.key);
      return match && match.count > 0
        ? { label: def.label, count: match.count, filter: def.key }
        : null;
    })
    .filter(Boolean) as { label: string; count: number; filter: string }[];

  if (items.length === 0) return null;

  return (
    <div className={styles.catStrip}>
      {/* Leading separator divides the category strip from the total-assets
          block to its left; the rest sit between categories. */}
      {items.map((item) => (
        <Fragment key={item.filter}>
          <span className={styles.catSep} aria-hidden />
          <div
            className={styles.catPill}
            onClick={() => window.open(assetUrl(`/explorer?filter_category=${item.filter}`), '_blank')}
          >
            <span className={styles.catPillCount}>{item.count.toLocaleString()}</span>
            <span className={styles.catPillLabel}>
              <span className={styles.catPillIcon} style={{ color: CATEGORY_COLORS[item.filter] }}>
                <CategoryIcon category={item.filter} />
              </span>
              <span className={styles.catPillName}>{item.label}</span>
            </span>
          </div>
        </Fragment>
      ))}
    </div>
  );
}

export function CategoryStripSkeleton() {
  return (
    <div className={styles.catStrip} aria-busy="true">
      {Array.from({ length: SKELETON_PILL_COUNT }).map((_, i) => (
        <span key={i} className={`${styles.skeleton} ${styles.skelCatPill}`} />
      ))}
    </div>
  );
}
